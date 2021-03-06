# Copyright 2019 The Pigweed Authors
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
"""Script that preprocesses a Python command then runs it."""

import argparse
import logging
import os
import pathlib
import re
import shlex
import subprocess
import sys

_LOG = logging.getLogger(__name__)


# Internally, all GN absolute paths start with a forward slash. This means that
# Windows absolute paths take the form
#
#   /C:/foo/bar
#
# These are not valid filesystem paths, and break if used. As this script has
# to duplicate GN's path resolution logic to convert internal paths to real
# filesystem paths, it has to try to detect strings of this form and correct
# them to well-formed paths.
#
# TODO(pwbug/110): This is the latest hack in a series of edge case handling
# implemented by this script, which is run on every string in sys.argv and could
# have unintended consequences. This script shouldn't have to exist--GN should
# standardize a way of finding a compiled binary for a build target.
def _resembles_internal_gn_windows_path(path: str) -> bool:
    return os.name == 'nt' and bool(re.match(r'^/[a-zA-Z]:[/\\]', path))


def _fix_windows_absolute_path(path: str) -> str:
    return path[1:] if _resembles_internal_gn_windows_path(path) else path


def parse_args() -> argparse.Namespace:
    """Parses arguments for this script, splitting out the command to run."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--gn-root',
        type=_fix_windows_absolute_path,
        required=True,
        help='Path to the root of the GN tree',
    )
    parser.add_argument(
        '--out-dir',
        type=_fix_windows_absolute_path,
        required=True,
        help='Path to the GN build output directory',
    )
    parser.add_argument(
        '--touch',
        type=_fix_windows_absolute_path,
        help='File to touch after command is run',
    )
    parser.add_argument(
        '--capture-output',
        action='store_true',
        help='Capture subcommand output; display only on error',
    )
    parser.add_argument(
        'command',
        nargs=argparse.REMAINDER,
        help='Python script with arguments to run',
    )
    return parser.parse_args()


def find_binary(target: pathlib.Path) -> str:
    """Tries to find a binary for a gn build target.

    Args:
        target: Relative filesystem path to the target's output directory and
            target name, separated by a colon.

    Returns:
        Full path to the target's binary.

    Raises:
        RuntimeError: No binary found for target.
    """

    target_dirname, target_name = target.name.rsplit(':', 1)

    for extension in ['', '.elf', '.exe']:
        potential_file = target.parent.joinpath(target_dirname,
                                                f'{target_name}{extension}')
        if potential_file.is_file():
            return str(potential_file)

    raise FileNotFoundError(
        f'Could not find output binary for build target {target}')


def _resolve_path(gn_root: str, out_dir: str, string: str) -> str:
    """Resolves a string to a filesystem path if it is a GN path.

    If the path specifies a GN target, attempts to find an compiled output
    binary for the target name.
    """

    string = _fix_windows_absolute_path(string)

    is_gn_path = string.startswith('//')
    is_out_path = string.startswith(out_dir)
    if not (is_gn_path or is_out_path):
        # If the string is not a path, do nothing.
        return string

    full_path = gn_root + string[2:] if is_gn_path else string
    resolved_path = pathlib.Path(full_path).resolve()

    # GN targets exist in the out directory and have the format
    # '/path/to/directory:target_name'.
    #
    # Pathlib interprets 'directory:target_name' as the filename, so check if it
    # contains a colon.
    if is_out_path and ':' in resolved_path.name:
        return find_binary(resolved_path)

    return str(resolved_path)


def resolve_path(gn_root: str, out_dir: str, string: str) -> str:
    """Resolves GN paths to filesystem paths in a semicolon-separated string.

    GN paths are assumed to be absolute, starting with "//". This is replaced
    with the relative filesystem path of the GN root directory.

    If the string is not a GN path, it is returned unmodified.

    If a path refers to the GN output directory and a target name is defined,
    attempts to locate a binary file for the target within the out directory.
    """
    return ';'.join(
        _resolve_path(gn_root, out_dir, path) for path in string.split(';'))


def main() -> int:
    """Script entry point."""

    args = parse_args()
    if not args.command or args.command[0] != '--':
        _LOG.error('%s requires a command to run', sys.argv[0])
        return 1

    try:
        resolved_command = [
            resolve_path(args.gn_root, args.out_dir, arg)
            for arg in args.command[1:]
        ]
    except FileNotFoundError as err:
        _LOG.error('%s: %s', sys.argv[0], err)
        return 1

    command = [sys.executable] + resolved_command
    _LOG.debug('RUN %s', shlex.join(command))

    if args.capture_output:
        completed_process = subprocess.run(
            command,
            # Combine stdout and stderr so that error messages are
            # correctly interleaved with the rest of the output.
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    else:
        completed_process = subprocess.run(command)

    if completed_process.returncode != 0:
        _LOG.debug('Command failed; exit code: %d',
                   completed_process.returncode)
        # TODO(pwbug/34): Print a cross-platform pastable-in-shell command, to
        # help users track down what is happening when a command is broken.
        if args.capture_output:
            sys.stdout.buffer.write(completed_process.stdout)
    elif args.touch:
        # If a stamp file is provided and the command executed successfully,
        # touch the stamp file to indicate a successful run of the command.
        touch_file = resolve_path(args.gn_root, args.out_dir, args.touch)
        _LOG.debug('TOUCH %s', touch_file)
        pathlib.Path(touch_file).touch()

    return completed_process.returncode


if __name__ == '__main__':
    sys.exit(main())
