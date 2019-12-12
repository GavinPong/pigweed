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
"""Script that invokes protoc to generate code for .proto files."""

import argparse
import logging
import sys

from typing import Optional

import pw_cli.log
import pw_cli.process

_LOG = logging.getLogger(__name__)

# Default protoc codegen plugins for each supported language.
# TODO(frolv): Make these overridable with a command-line argument.
DEFAULT_PROTOC_PLUGINS = {
    # TODO(frolv): Enable this when porting the pw_protobuf module.
    # 'cc': 'protoc-gen-custom=pw_protobuf_codegen',
}


def argument_parser(
    parser: Optional[argparse.ArgumentParser] = None
) -> argparse.ArgumentParser:
    """Registers the script's arguments on an argument parser."""

    if parser is None:
        parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument('--language', default='cc', help='Output language')
    parser.add_argument('--module-path',
                        required=True,
                        help='Path to the module containing the .proto files')
    parser.add_argument('--out-dir',
                        help='Output directory for generated code')
    parser.add_argument('protos',
                        metavar='PROTO',
                        nargs='+',
                        help='Input protobuf files')

    return parser


def main() -> int:
    """Runs protoc as configured by command-line arguments."""

    args = argument_parser().parse_args()

    try:
        protoc_plugin = DEFAULT_PROTOC_PLUGINS[args.language]
    except KeyError:
        _LOG.error('Unsupported language: %s', args.language)
        return 1

    return pw_cli.process.run(
        'protoc',
        '--plugin',
        protoc_plugin,
        '-I',
        args.module_path,
        '--custom_out',
        args.out_dir,
        *args.protos,
    )


if __name__ == '__main__':
    pw_cli.log.install()
    sys.exit(main())
