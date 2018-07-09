
from distutils.util import strtobool
from json import loads
from os import devnull
from subprocess import check_output
from sys import executable
from warnings import warn

import packaging.version
import pytest
from pkg_resources import get_distribution
import pip_api

max_version = packaging.version.parse('18.1.0')
pip_version = packaging.version.parse(pip_api.version())
if pip_version > max_version:
    warn(
        'Version pip=={} is possibly incompatible, highest '
        'known compatible version is {}.'.format(
            pip_version, max_version
        )
    )

__version__ = '0.0.4'

DEFAULT_PATTERNS = [
    'req*.txt', 'req*.pip', 'requirements/*.txt', 'requirements/*.pip'
]
_installed_requirements = None


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


def get_installed_distributions():
    global _installed_requirements
    if not _installed_requirements:
        _installed_distributions = pip_api.installed_distributions()
    return _installed_distributions


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

    def __init__(self, filename, parent, config=None, session=None,
                 nodeid=None, installed_distributions=None):
        super(ReqsFile, self).__init__(
            filename, parent, config=config, session=session
        )
        self.filename = str(filename)
        if not installed_distributions:
            installed_distributions = get_installed_distributions()
        self.installed_distributions = installed_distributions
        self.pip_outdated_dists = None

    def get_requirements(self):
        try:
            return pip_api.parse_requirements(
                self.filename, options=PipOption(self.config)
            )
        except pip_api.exceptions.PipError as e:
            raise ReqsError('%s (from -r %s)' % (
                e.args[0].split('\n')[0],
                self.filename,
            ))

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
