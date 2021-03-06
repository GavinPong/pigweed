#!/usr/bin/env python
# Copyright 2020 The Pigweed Authors
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations under
# the License.
"""Installs or updates prebuilt tools.

Must be tested with Python 2 and Python 3.

The stdout of this script is meant to be executed by the invoking shell.
"""

from __future__ import print_function

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile


def parse(argv=None):
    """Parse arguments."""

    script_root = os.path.join(os.environ['PW_ROOT'], 'pw_env_setup', 'py',
                               'pw_env_setup', 'cipd_setup')
    git_root = subprocess.check_output(
        ('git', 'rev-parse', '--show-toplevel'),
        cwd=script_root,
    ).decode('utf-8').strip()

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        '--install-dir',
        dest='root_install_dir',
        default=os.path.join(git_root, '.cipd'),
    )
    parser.add_argument('--package-file',
                        dest='package_files',
                        action='append')
    parser.add_argument('--cipd',
                        default=os.path.join(script_root, 'wrapper.py'))
    parser.add_argument('--cache-dir',
                        default=os.environ.get(
                            'CIPD_CACHE_DIR',
                            os.path.expanduser('~/.cipd-cache-dir')))

    return parser.parse_args(argv)


def check_auth(cipd, paths=('pigweed', )):
    """Check have access to CIPD pigweed directory."""

    try:
        subprocess.check_output([cipd, 'auth-info'], stderr=subprocess.STDOUT)
        logged_in = True
    except subprocess.CalledProcessError:
        logged_in = False

    for path in paths:
        # Not catching CalledProcessError because 'cipd ls' seems to never
        # return an error code.
        output = subprocess.check_output([cipd, 'ls', path],
                                         stderr=subprocess.STDOUT).decode()
        if 'No matching packages' in output:
            print()
            print('=' * 60, file=sys.stderr)
            if logged_in:
                print('ERROR: no access to CIPD path "{}":'.format(path),
                      file=sys.stderr)
            else:
                print('ERROR: no access to CIPD path "{}", try logging in '
                      'with this command:'.format(path),
                      file=sys.stderr)
                print(cipd, 'auth-login', file=sys.stderr)
            print('=' * 60, file=sys.stderr)
            return False

    return True


def write_ensure_file(package_file, ensure_file):
    with open(package_file, 'r') as ins:
        data = json.load(ins)

    # TODO(pwbug/103) Remove 30 days after bug fixed.
    if os.path.isdir(ensure_file):
        shutil.rmtree(ensure_file)

    with open(ensure_file, 'w') as outs:
        outs.write('$VerifiedPlatform linux-amd64\n'
                   '$VerifiedPlatform mac-amd64\n'
                   '$ParanoidMode CheckPresence\n')

        for entry in data:
            outs.write('{} {}\n'.format(entry['path'],
                                        ' '.join(entry['tags'])))


def update(
    cipd,
    package_files,
    root_install_dir,
    cache_dir,
    env_vars=None,
):
    """Grab the tools listed in ensure_files."""

    if not check_auth(cipd):
        return False

    # TODO(mohrr) use os.makedirs(..., exist_ok=True).
    if not os.path.isdir(root_install_dir):
        os.makedirs(root_install_dir)

    if env_vars:
        env_vars.prepend('PATH', root_install_dir)
        env_vars.set('PW_CIPD_INSTALL_DIR', root_install_dir)
        env_vars.set('CIPD_CACHE_DIR', cache_dir)

    pw_root = None
    if env_vars:
        pw_root = env_vars.get('PW_ROOT', None)
    if not pw_root:
        pw_root = os.environ['PW_ROOT']

    # Run cipd for each json file.
    default_packages = os.path.join(pw_root, 'pw_env_setup', 'py',
                                    'pw_env_setup', 'cipd_setup', '*.json')
    for package_file in package_files or glob.glob(default_packages):
        ensure_file = os.path.join(
            root_install_dir,
            os.path.basename(os.path.splitext(package_file)[0] + '.ensure'))
        write_ensure_file(package_file, ensure_file)
        install_dir = os.path.join(
            root_install_dir,
            os.path.basename(os.path.splitext(package_file)[0]))

        cmd = [
            cipd,
            'ensure',
            '-ensure-file', ensure_file,
            '-root', install_dir,
            '-log-level', 'warning',
            '-max-threads', '0',  # 0 means use CPU count.
        ]  # yapf: disable

        # TODO(pwbug/135) Use function from common utility module.
        with tempfile.TemporaryFile(mode='w+') as temp:
            print(*cmd, file=temp)
            try:
                subprocess.check_call(cmd,
                                      stdout=temp,
                                      stderr=subprocess.STDOUT)
            except subprocess.CalledProcessError:
                temp.seek(0)
                sys.stderr.write(temp.read())
                raise

        # Set environment variables so tools can later find things under, for
        # example, 'share'.
        name = os.path.basename(install_dir)

        if env_vars:
            # Some executables get installed at top-level and some get
            # installed under 'bin'.
            env_vars.prepend('PATH', install_dir)
            env_vars.prepend('PATH', os.path.join(install_dir, 'bin'))
            env_vars.set('PW_{}_CIPD_INSTALL_DIR'.format(name.upper()),
                         install_dir)

            # Windows has its own special toolchain.
            if os.name == 'nt':
                env_vars.prepend('PATH',
                                 os.path.join(install_dir, 'mingw64', 'bin'))

    return True


if __name__ == '__main__':
    update(**vars(parse()))
    sys.exit(0)
