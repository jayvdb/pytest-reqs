from glob import glob
from itertools import chain

from pip import get_installed_distributions
from pip.download import PipSession
from pip.exceptions import InstallationError
from pip.req import parse_requirements
from pip.commands.list import ListCommand
import pytest


__version__ = '0.0.4'

DEFAULT_PATTERNS = [
    'req*.txt', 'req*.pip', 'requirements/*.txt', 'requirements/*.pip'
]


def pytest_addoption(parser):
    group = parser.getgroup("general")
    group.addoption(
        '--reqs', action='store_true',
        help="check requirements files against what is installed"
    )
    group.addoption(
        '--reqs-outdated', action='store_true',
        help="check requirements files for updates"
    )
    parser.addini(
        "reqsignorelocal",
        help="ignore local requirements (default: False)",
    )
    parser.addini(
        "reqsfilenamepatterns",
        help="Override the default filename patterns to search (default:"
             "req*.txt, req*.pip, requirements/*.txt, requirements/*.pip)",
        type="linelist",
    )


def pytest_sessionstart(session):
    config = session.config
    config.ignore_local = config.getini("reqsignorelocal").lower() == 'true'
    config.patterns = config.getini("reqsfilenamepatterns")


def pytest_collection_modifyitems(config, session, items):
    if config.option.reqs:
        check_requirements(
            config, session, items, get_installed_distributions(), ReqsItem
        )
    if config.option.reqs_outdated:
        list_cmd = ListCommand()
        options, args = list_cmd.parse_args([])
        outdated_distributions = list_cmd.get_outdated(
            get_installed_distributions(), options
        )
        check_requirements(
            config, session, items, outdated_distributions, OutdatedReqsItem
        )


def check_requirements(config, session, items, dists, reqs_item_cls):
    patterns = config.patterns or DEFAULT_PATTERNS
    filenames = set(chain.from_iterable(map(glob, patterns)))
    distributions = dict((d.project_name.lower(), d) for d in dists)

    items.extend(
        reqs_item_cls(filename, distributions, config, session)
        for filename in filenames
    )


class PipOption:
    def __init__(self, config):
        self.skip_requirements_regex = '^-e' if config.ignore_local else ''
        self.isolated_mode = False
        self.default_vcs = None


class ReqsError(Exception):
    """ indicates an error during requirements checks. """


class ReqsItem(pytest.Item, pytest.File):

    def __init__(self, filename, installed_distributions, config, session):
        super(ReqsItem, self).__init__(
            filename, config=config, session=session
        )
        self.add_marker("reqs")
        self.filename = filename
        self.installed_distributions = installed_distributions
        self.config = config

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
            raise ReqsError("%s (from -r %s)" % (
                e.args[0].split('\n')[0],
                self.filename,
            ))
        return name_to_req

    def runtest(self):
        for name, req in self.get_requirements().items():
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

    def repr_failure(self, excinfo):
        if excinfo.errisinstance(ReqsError):
            return excinfo.value.args[0]
        return super(ReqsItem, self).repr_failure(excinfo)

    def reportinfo(self):
        return (self.fspath, -1, "requirements-check")


class OutdatedReqsItem(ReqsItem):
    def __init__(self, filename, outdated_distributions, config, session):
        super(ReqsItem, self).__init__(
            filename, config=config, session=session
        )
        self.add_marker("reqs-outdated")
        self.filename = filename
        self.outdated_distributions = outdated_distributions
        self.config = config

    def runtest(self):
        requirements = self.get_requirements()
        for name, req in self.outdated_distributions.items():
            if name in requirements:
                raise ReqsError(
                    'Distribution "%s" is outdated (%s -> %s)' % (
                        name,
                        req.version,
                        req.latest_version
                    ))
