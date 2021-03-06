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
"""Builds and manages databases of tokenized strings."""

import collections
import csv
from datetime import datetime
import io
import logging
import re
import struct
from typing import BinaryIO, Callable, Dict, Iterable, List, NamedTuple
from typing import Optional, Tuple, Union, ValuesView

DATE_FORMAT = '%Y-%m-%d'

DEFAULT_HASH_LENGTH = 96
TOKENIZER_HASH_CONSTANT = 65599

_LOG = logging.getLogger('pw_tokenizer')


def _value(char: Union[int, str]) -> int:
    return char if isinstance(char, int) else ord(char)


def pw_tokenizer_65599_fixed_length_hash(string: Union[str, bytes],
                                         hash_length: int) -> int:
    """Hashes the provided string."""
    hash_value = len(string)
    coefficient = TOKENIZER_HASH_CONSTANT

    for char in string[:hash_length]:
        hash_value = (hash_value + coefficient * _value(char)) % 2**32
        coefficient = (coefficient * TOKENIZER_HASH_CONSTANT) % 2**32

    return hash_value


def default_hash(string: Union[str, bytes]) -> int:
    return pw_tokenizer_65599_fixed_length_hash(string, DEFAULT_HASH_LENGTH)


class TokenizedStringEntry:
    """A tokenized string with its metadata."""
    def __init__(self,
                 token: int,
                 string: str,
                 date_removed: Optional[datetime] = None):
        self.token = token
        self.string = string
        self.date_removed = date_removed

    def key(self) -> Tuple[int, str]:
        """The key determines uniqueness for a tokenized string."""
        return self.token, self.string

    def update_date_removed(self,
                            new_date_removed: Optional[datetime]) -> None:
        """Sets self.date_removed if the other date is newer."""
        # No removal date (None) is treated as the newest date.
        if self.date_removed is None:
            return

        if new_date_removed is None or new_date_removed > self.date_removed:
            self.date_removed = new_date_removed

    def __lt__(self, other) -> bool:
        """Sorts the entry by token, date removed, then string."""
        if self.token != other.token:
            return self.token < other.token

        # Sort removal dates in reverse, so the most recently removed (or still
        # present) entry appears first.
        if self.date_removed != other.date_removed:
            return (other.date_removed or datetime.max) < (self.date_removed
                                                           or datetime.max)

        return self.string < other.string

    def __str__(self) -> str:
        return self.string

    def __repr__(self) -> str:
        return '{}({!r})'.format(type(self).__name__, self.string)


class Database:
    """Database of tokenized strings stored as TokenizedStringEntry objects."""
    def __init__(self,
                 entries: Iterable[TokenizedStringEntry] = (),
                 tokenize: Callable[[str], int] = default_hash):
        """Creates a token database."""
        # The database dict stores each unique (token, string) entry.
        self._database: dict = {entry.key(): entry for entry in entries}
        self.tokenize = tokenize

        # This is a cache for fast token lookup that is built as needed.
        self._cache: Optional[Dict[int, List[TokenizedStringEntry]]] = None

    @classmethod
    def from_strings(
            cls,
            strings: Iterable[str],
            tokenize: Callable[[str], int] = default_hash) -> 'Database':
        """Creates a Database from an iterable of strings."""
        return cls((TokenizedStringEntry(tokenize(string), string)
                    for string in strings), tokenize)

    @classmethod
    def merged(cls, *databases: 'Database') -> 'Database':
        """Creates a TokenDatabase from one or more other databases."""
        db = cls()
        db.merge(*databases)
        return db

    @property
    def token_to_entries(self) -> Dict[int, List[TokenizedStringEntry]]:
        """Returns a dict that maps tokens to a list of TokenizedStringEntry."""
        if self._cache is None:  # build cache token -> entry cache
            self._cache = collections.defaultdict(list)
            for entry in self._database.values():
                self._cache[entry.token].append(entry)

        return self._cache

    def entries(self) -> ValuesView[TokenizedStringEntry]:
        """Returns iterable over all TokenizedStringEntries in the database."""
        return self._database.values()

    def collisions(self) -> Tuple[Tuple[int, List[TokenizedStringEntry]], ...]:
        """Returns tuple of (token, entries_list)) for all colliding tokens."""
        return tuple((token, entries)
                     for token, entries in self.token_to_entries.items()
                     if len(entries) > 1)

    def mark_removals(
        self,
        all_strings: Iterable[str],
        removal_date: Optional[datetime] = None
    ) -> List[TokenizedStringEntry]:
        """Marks strings missing from all_strings as having been removed.

        The strings are assumed to represent the complete set of strings for the
        database. Strings currently in the database not present in the provided
        strings are marked with a removal date but remain in the database.
        Strings in all_strings missing from the database are NOT added; call the
        add function to add these strings.

        Args:
          all_strings: the complete set of strings present in the database
          removal_date: the datetime for removed entries; today by default

        Returns:
          A list of entries marked as removed.
        """
        self._cache = None

        if removal_date is None:
            removal_date = datetime.now()

        all_strings = frozenset(all_strings)  # for faster lookup

        removed = []

        # Mark this entry as having been removed from the ELF.
        for entry in self._database.values():
            if (entry.string not in all_strings
                    and (entry.date_removed is None
                         or removal_date < entry.date_removed)):
                # Add a removal date, or update it to the oldest date.
                entry.date_removed = removal_date
                removed.append(entry)

        return removed

    def add(self, strings: Iterable[str]) -> None:
        """Adds new strings to the database."""
        self._cache = None

        # Add new and update previously removed entries.
        for string in strings:
            key = self.tokenize(string), string

            try:
                entry = self._database[key]
                if entry.date_removed:
                    entry.date_removed = None
            except KeyError:
                self._database[key] = TokenizedStringEntry(key[0], string)

    def purge(
        self,
        date_removed_cutoff: Optional[datetime] = None
    ) -> List[TokenizedStringEntry]:
        """Removes and returns entries removed on/before date_removed_cutoff."""
        self._cache = None

        if date_removed_cutoff is None:
            date_removed_cutoff = datetime.max

        to_delete = [
            key for key, entry in self._database.items()
            if entry.date_removed and entry.date_removed <= date_removed_cutoff
        ]

        for key in to_delete:
            del self._database[key]

        return to_delete

    def merge(self, *databases: 'Database') -> None:
        """Merges two or more databases together, keeping the newest dates."""
        self._cache = None

        for other_db in databases:
            for entry in other_db.entries():
                key = entry.key()

                if key in self._database:
                    self._database[key].update_date_removed(entry.date_removed)
                else:
                    self._database[key] = entry

    def filter(self, include: Iterable = (), exclude: Iterable = ()) -> None:
        """Filters the database using regular expressions (strings or compiled).

    Args:
      include: iterable of regexes; only entries matching at least one are kept
      exclude: iterable of regexes; entries matching any of these are removed
    """
        self._cache = None

        to_delete: List[Tuple] = []

        if include:
            include_re = [re.compile(pattern) for pattern in include]
            to_delete.extend(
                key for key, val in self._database.items()
                if not any(rgx.search(val.string) for rgx in include_re))

        if exclude:
            exclude_re = [re.compile(pattern) for pattern in exclude]
            to_delete.extend(key for key, val in self._database.items()  #
                             if any(
                                 rgx.search(val.string) for rgx in exclude_re))

        for key in to_delete:
            del self._database[key]

    def __len__(self) -> int:
        """Returns the number of entries in the database."""
        return len(self.entries())

    def __str__(self) -> str:
        """Outputs the database as CSV."""
        csv_output = io.BytesIO()
        write_csv(self, csv_output)
        return csv_output.getvalue().decode()


def parse_csv(fd) -> Iterable[TokenizedStringEntry]:
    """Parses TokenizedStringEntries from a CSV token database file."""
    for line in csv.reader(fd):
        try:
            token_str, date_str, string_literal = line

            token = int(token_str, 16)
            date = (datetime.strptime(date_str, DATE_FORMAT)
                    if date_str.strip() else None)

            yield TokenizedStringEntry(token, string_literal, date)
        except (ValueError, UnicodeDecodeError) as err:
            _LOG.error('Failed to parse tokenized string entry %s: %s', line,
                       err)


def write_csv(database: Database, fd: BinaryIO) -> None:
    """Writes the database as CSV to the provided binary file."""
    for entry in sorted(database.entries()):
        # Align the CSV output to 10-character columns for improved readability.
        # Use \n instead of RFC 4180's \r\n.
        fd.write('{:08x},{:10},"{}"\n'.format(
            entry.token,
            entry.date_removed.strftime(DATE_FORMAT) if entry.date_removed else
            '', entry.string.replace('"', '""')).encode())  # escape " as ""


class _BinaryFileFormat(NamedTuple):
    """Attributes of the binary token database file format."""

    magic: bytes = b'TOKENS\0\0'
    header: struct.Struct = struct.Struct('<8sI4x')
    entry: struct.Struct = struct.Struct('<IBBH')


BINARY_FORMAT = _BinaryFileFormat()


def file_is_binary_database(fd: BinaryIO) -> bool:
    """True if the file starts with the binary token database magic string."""
    try:
        fd.seek(0)
        magic = fd.read(len(BINARY_FORMAT.magic))
        fd.seek(0)
        return BINARY_FORMAT.magic == magic
    except IOError:
        return False


def parse_binary(fd: BinaryIO) -> Iterable[TokenizedStringEntry]:
    """Parses TokenizedStringEntries from a binary token database file."""
    magic, entry_count = BINARY_FORMAT.header.unpack(
        fd.read(BINARY_FORMAT.header.size))

    if magic != BINARY_FORMAT.magic:
        raise ValueError(
            'Magic number mismatch (found {!r}, expected {!r})'.format(
                magic, BINARY_FORMAT.magic))

    entries = []

    for _ in range(entry_count):
        token, day, month, year = BINARY_FORMAT.entry.unpack(
            fd.read(BINARY_FORMAT.entry.size))

        try:
            date_removed: Optional[datetime] = datetime(year, month, day)
        except ValueError:
            date_removed = None

        entries.append((token, date_removed))

    # Read the entire string table and define a function for looking up strings.
    string_table = fd.read()

    def read_string(start):
        end = string_table.find(b'\0', start)
        return string_table[start:string_table.find(b'\0', start)].decode(
        ), end + 1

    offset = 0
    for token, removed in entries:
        string, offset = read_string(offset)
        yield TokenizedStringEntry(token, string, removed)


def write_binary(database: Database, fd: BinaryIO) -> None:
    """Writes the database as packed binary to the provided binary file."""
    entries = sorted(database.entries())

    fd.write(BINARY_FORMAT.header.pack(BINARY_FORMAT.magic, len(entries)))

    string_table = bytearray()

    for entry in entries:
        if entry.date_removed:
            removed_day = entry.date_removed.day
            removed_month = entry.date_removed.month
            removed_year = entry.date_removed.year
        else:
            # If there is no removal date, use the special value 0xffffffff for
            # the day/month/year. That ensures that still-present tokens appear
            # as the newest tokens when sorted by removal date.
            removed_day = 0xff
            removed_month = 0xff
            removed_year = 0xffff

        string_table += entry.string.encode()
        string_table.append(0)

        fd.write(
            BINARY_FORMAT.entry.pack(entry.token, removed_day, removed_month,
                                     removed_year))

    fd.write(string_table)


class DatabaseFile(Database):
    """A token database that is associated with a particular file.

  This class adds the write_to_file() method that writes to file from which it
  was created in the correct format (CSV or binary).
  """
    def __init__(self, path: str):
        self.path = path

        # Read the path as a packed binary file.
        with open(self.path, 'rb') as fd:
            if file_is_binary_database(fd):
                super().__init__(parse_binary(fd))
                self._export = write_binary
                return

        # Read the path as a CSV file.
        with open(self.path, 'r', newline='') as file:
            super().__init__(parse_csv(file))
            self._export = write_csv

    def write_to_file(self, path: Optional[str] = None) -> None:
        """Exports in the original format to the original or provided path."""
        with open(self.path if path is None else path, 'wb') as fd:
            self._export(self, fd)
