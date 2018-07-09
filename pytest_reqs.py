
from distutils.util import strtobool
from json import loads
from os import devnull
from subprocess import check_output
from sys import executable
from warnings import warn

import packaging.version
import pytest
from pkg_resources import get_distribution

max_version = packaging.version.parse('9.0.2')
pip_version = packaging.version.parse(get_distribution('pip').version)
if pip_version > max_version:
    warn(
        'Version pip=={} is possibly incompatible, highest '
        'known compatible version is {}.'.format(
            pip_version, max_version
        )
    )

from pip import get_installed_distributions  # noqa
from pip.download import PipSession  # noqa
from pip.exceptions import InstallationError  # noqa
from pip.req import parse_requirements  # noqa


__version__ = '0.0.4'

DEFAULT_PATTERNS = [
    'req*.txt', 'req*.pip', 'requirements/*.txt', 'requirements/*.pip'
]


def pytest_addoption(parser):
    group = parser.getgroup('general')
    group.addoption(
        '--reqs', action='store_true',
        help='check requirements files against what is installed'
    )
    group.addoption(
        '--reqs-outdated', action='store_true',
        help='check requirements files for updates'
    )
    parser.addini(
        'reqsignorelocal',
        help='ignore local requirements (default: False)',
    )
    parser.addini(
        'reqsfilenamepatterns',
        help='Override the default filename patterns to search (default:'
             'req*.txt, req*.pip, requirements/*.txt, requirements/*.pip)',
        type='linelist',
    )


def pytest_sessionstart(session):
    config = session.config
    if not hasattr(config, 'ignore_local'):
        ignore_local = config.getini('reqsignorelocal') or 'no'
        config.ignore_local = strtobool(ignore_local)
    if not hasattr(config, 'patterns'):
        config.patterns = config.getini('reqsfilenamepatterns')


def pytest_collect_file(parent, path):
    config = parent.config
    if _is_requirements(config, path):
        return ReqsFile(path, parent)


def _is_requirements(config, path):
    globs = config.patterns or DEFAULT_PATTERNS
    for glob in globs:
        if path.check(fnmatch=glob):
            return True
    return False


def get_installed_distributions_mapping():
    return dict(
        (d.project_name.lower(), d)
        for d in get_installed_distributions()
    )


def get_outdated_requirements():
    local_pip_version = packaging.version.parse(
        get_distribution('pip').version
    )
    required_pip_version = packaging.version.parse('9.0.0')

    if local_pip_version >= required_pip_version:
        with open(devnull, 'w') as DEVNULL:
            pip_outdated_dists = loads(check_output(
                [executable, '-m', 'pip', 'list', '-o', '--format', 'json'],
                stderr=DEVNULL
            ))

            return pip_outdated_dists


class PipOption:
    def __init__(self, config):
        self.skip_requirements_regex = '^-e' if config.ignore_local else ''
        self.isolated_mode = False
        self.default_vcs = None


class ReqsError(Exception):
    """ indicates an error during requirements checks. """


class ReqsBase(object):

    def repr_failure(self, excinfo):
        if excinfo.errisinstance(ReqsError):
            return excinfo.value.args[0]
        return super(ReqsBase, self).repr_failure(excinfo)

    def reportinfo(self):
        return (self.fspath, -1, 'requirements-check')


class ReqsFile(ReqsBase, pytest.File):

    def __init__(self, fspath, parent, config=None, session=None,
                 nodeid=None, installed_distributions=None):
        super(ReqsFile, self).__init__(
            fspath, parent, config=config, session=session, nodeid=nodeid,
        )
        self.filename = str(fspath)
        if not installed_distributions:
            installed_distributions = get_installed_distributions_mapping()
        self.installed_distributions = installed_distributions
        self.pip_outdated_dists = None

    def get_requirements(self):
        reqs = parse_requirements(
            self.filename, session=PipSession(), options=PipOption(self.config)
        )
        try:
            name_to_req = dict(
                (r.name.lower(), r)
                for r in reqs
                if r.name and self.filename in r.comes_from
            )
        except InstallationError as e:
            raise ReqsError('%s (from -r %s)' % (
                e.args[0].split('\n')[0],
                self.filename,
            ))
        return name_to_req

    def collect(self):
        if self.config.option.reqs_outdated:
            self.pip_outdated_dists = get_outdated_requirements()

        for name, req in self.get_requirements().items():
            if self.config.option.reqs:
                yield ReqsItem(name, self, req)
            if self.config.option.reqs_outdated:
                yield OutdatedReqsItem(name, self, req)


class ReqsItem(ReqsBase, pytest.Item):

    def __init__(self, name, parent, requirement):
        super(ReqsItem, self).__init__(name, parent)
        self.add_marker('reqs')
        self.requirement = requirement
        self.installed_distributions = parent.installed_distributions

    def runtest(self):
        name = self.name
        req = self.requirement
        try:
            installed_distribution = self.installed_distributions[name]
        except KeyError:
            raise ReqsError(
                'Distribution "%s" is not installed' % (name)
            )
        if not req.specifier.contains(installed_distribution.version):
            raise ReqsError(
                'Distribution "%s" requires %s but %s is installed' % (
                    installed_distribution.project_name,
                    req,
                    installed_distribution.version,
                ))


class OutdatedReqsItem(ReqsItem):

    def __init__(self, name, parent, requirement):
        super(OutdatedReqsItem, self).__init__(name, parent, requirement)
        self.add_marker('reqs-outdated')

    def runtest(self):
        name = self.name
        req = self.requirement
        for dist in self.parent.pip_outdated_dists:
            if name == dist['name']:
                raise ReqsError(
                    'Distribution "%s" is outdated (from %s), '
                    'latest version is %s==%s' % (
                        name,
                        req.comes_from,
                        name,
                        dist['latest_version']
                    ))
