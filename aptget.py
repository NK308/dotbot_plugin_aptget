# coding=utf-8
import apt
from aptsources.sourceslist import SourcesList, SourceEntry
import os
import re
import sys
import subprocess

import dotbot


class AptGet(dotbot.Plugin):
    _directive = 'aptget'

    def __init__(self, context):
        super(AptGet, self).__init__(self)
        self._apt_cache = apt.Cache()

    def can_handle(self, directive):
        return directive == self._directive

    def handle(self, directive, data):
        if directive != self._directive:
            raise ValueError('AptGet cannot handle directive %s' %
                directive)
        return self._process_packages(data)

    def _process_packages(self, packages):
        if os.geteuid() != 0:
            self._log.error('Need root permissions to install packages')
            raise AptGetError('Need root permissions to install packages')
        # TODO: think about this: defaults = self._context.defaults().get('apt-get', {})
        success = True
        cleaned_packages = self._dispatch_names_and_sources(packages)
        if cleaned_packages['sources']:
            sourcesList = SourcesList()
            for source in cleaned_packages['sources']:
                self._add_source(sourcesList, source)
            sourcesList.save()
        self._apt_cache._list.read_main_list()
        self._apt_cache.update()
        self._apt_cache.open()  # NB: utilize updated cache http://apt.alioth.debian.org/python-apt-doc/library/apt.cache.html#apt.cache.Cache.update
        for pkg_name, upgrade in cleaned_packages['packages'].items():
            success &= self._mark_package_install_upgrade(pkg_name, upgrade)
        try:
            success &= self._apt_cache.commit()
        except Exception as e:
            self._log.error('Failed to install packages: %s' % e)
            success = False
        if success:
            self._log.info('All packages have been installed')
        else:
            self._log.error('Some packages were not successfully installed')
        return success

    def _dispatch_names_and_sources(self, packages):
        '''
        Returns cleaned dict with list of sources and dict of packages.
        {"sources": [], "packages": {"packaga_name": "upgrade"}}
        '''
        cleaned_dict = {'sources': [], 'packages': {}}
        if isinstance(packages, str):
            cleaned_dict['packages'][packages] = False
        elif isinstance(packages, list):
            for pkg_name in packages:
                cleaned_dict['packages'][pkg_name] = False
        elif isinstance(packages, dict):
            new_syntax = False
            for key, value in packages.items():
                if key == "packages" and isinstance(value, list):
                    continue
                if key == "sources" and isinstance(value, list):
                    continue
                if key == "update" and isinstance(value, bool):
                    continue
                break
            else:
                new_syntax = True
            if new_syntax:
                #new syntax, with different dicts for 
                cleaned_dict["sources"] = packages.get("sources", dict())
                for paket in packages.get("packages", list()):
                    cleaned_dict["packages"][paket] = packages.get("update", False)
            else:
                #old syntax, with sources as package options
                for pkg_name, pkg_opts in packages.items():
                    if isinstance(pkg_opts, dict):
                        if 'ppa_source' in pkg_opts.keys():
                            cleaned_dict['sources'].append(pkg_opts['ppa_source'])
                        cleaned_dict['packages'][pkg_name] = pkg_opts.get('upgrade', False)
                    else:
                        if pkg_opts:
                            cleaned_dict['sources'].append(pkg_opts)
                        cleaned_dict['packages'][pkg_name] = False
        return cleaned_dict

    def _get_codename(self):
        with open("/etc/os-release") as f:
            m = map(lambda l: re.match(r"VERSION_CODENAME=(.*)", l), f)
            return list(filter(lambda l: l is not None, m))[0].group(1)

    def _add_source(self, sourcesList, source):
        '''
        Add PPA source by passing it to a shell-command "add-apt-repository" via subprocess.
        Reimplementing "add-apt-repository" script logic is too overwhelming for our purpose.
        Returns True if successfully added PPA source, else False.
        '''
        rppa = re.compile(r"^ppa:([0-9a-zA-Z_-]+)/([0-9a-zA-Z_-]+)$")
        rfull = re.compile(r"^(?P<type>deb(?:-src)?) (?:\[(?P<options>.*)\] )?(?P<uri>(?P<protocol>(?:(?:mirror\+)?(?P<local>file|cdrom|copy)|(?P<remote>http|https|ftp|ssh))):(?(remote)//((?:(?!-)[a-zA-Z0-9-]{1,63}(?<!-)\.)+[a-zA-Z]{2,6}))/[a-zA-Z0-9-_\./]+) (?P<suite>[a-z/]+)(?:(?<!/) (?P<components>[a-z]+(?: [a-z]+)*))$")

        mppa = rppa.match(source)
        mfull = rfull.match(source)
        if mppa is not None:
            username = mppa.group(1)
            reponame = mppa.group(2)
            sourcesList.add("deb", "http://ppa.launchpad.net/{}/{}/ubuntu".format(username, reponame), self._get_codename(), ["main"], file="/etc/apt/sources.list.d/{}-{}.list".format(username, reponame))
            sourcesList.add("#deb-src", "http://deb.launchpad.net/{}/{}/ubuntu".format(username, reponame), self._get_codename(), ["main"], file="/etc/apt/sources.list.d/{}-{}.list".format(username, reponame))
            self._log.info("ppa added")
            return True
        elif mfull is not None:
            if mfull.group('options') is not None:
                # python-apt isn't able to handle options
                return False
            if mfull.group('components') is not None:
                components = mfull.group('components').split(' ')
            else:
                components = list()
            sourcesList.add(mfull.group('type'), mfull.group('uri'), mfull.group('suite'), components, file="/etc/apt/sources.list.d/dotbot-managed.list")
            self._log.info("full source added")
            return True
        else:
            self._log.error("invalid ppa not added")
            return False

    def _mark_package_install_upgrade(self, pkg_name, upgrade):
        success = False
        if pkg_name in self._apt_cache:
            if not self._apt_cache[pkg_name].is_installed:
                self._apt_cache[pkg_name].mark_install()
                self._log.lowinfo('Package %s marked for install at version %s' %
                                  (pkg_name, self._apt_cache[pkg_name].candidate.version))
                success = True
            else:
                if not upgrade:
                    self._log.info('Package %s is already installed' % pkg_name)
                    success = True
                else:
                    if self._apt_cache[pkg_name].is_upgradable:
                        self._apt_cache[pkg_name].mark_upgrade()
                        self._log.lowinfo('Package %s marked for upgrade: %s -> %s' %
                                          (pkg_name, self._apt_cache[pkg_name].installed.version,
                                           self._apt_cache[pkg_name].candidate.version))
                        success = True
                    else:
                        self._log.lowinfo('Package %s is installed at version %s and have no candidates to upgrade' %
                                          (pkg_name, self._apt_cache[pkg_name].installed.version))
                        success = True
        else:
            found_entries = self._search_in_cache(pkg_name)
            if found_entries:
                self._log.lowinfo('Unable to locate package %s. Maybe you meant one of those: %s' % found_entries)
                success = False
            else:
                self._log.error('Unable to locate package %s' % pkg_name)
                success = False
        return success

    def _search_in_cache(self, pkg_name):
        '''
        Returns list of entries if package name found in current cache, else None
        '''
        found_entries = [pkg for pkg in self._apt_cache.keys() if pkg_name in pkg]
        if found_entries:
            return found_entries
        else:
            return None


class AptGetError(Exception):
    pass
