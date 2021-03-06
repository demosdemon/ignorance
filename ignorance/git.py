# -*- coding: utf-8 -*-
import itertools
import os
import re
from . import utils
try:
    # scandir-powered walk is in the stdlib in python 3.5+
    from os import scandir  # NOQA
    from os import walk as _walk
except ImportError:
    # otherwise, grab it from scandir for the performance boost
    from scandir import walk as _walk
try:
    # pathlib is in python stdlib in python 3.5+
    from pathlib import Path
except ImportError:
    from pathlib2 import Path


def ancestor_vcs_directory(filepath, dirname='.git'):
    """
    Find the closet parent directory containing a magic VCS directory.
    """
    orig_path = filepath
    filepath = os.path.expanduser(filepath)
    if not os.path.exists(filepath):
        raise ValueError("{} does not exist".format(orig_path))
    # Edge case
    if os.path.isdir(filepath) and os.path.split(filepath)[1] == '.git':
        return filepath
    path = Path(os.path.abspath(filepath))
    if path.is_file():
        path = path.parent
    parents = list(path.parents)
    parents.reverse()
    found = None
    current_dir = path
    while not found and parents:
        test = current_dir / dirname
        if test.exists() and test.is_dir():
            found = str(current_dir)
        else:
            current_dir = parents.pop()
    return found


def walk(directory, onerror=None, filename='.gitignore',
         overrides=None, ignore_completely=None):
    """
    Generate the file names in a directory tree by walking the tree
    top-down, while obeying the rules of .gitignore. Links will not
    be followed.
    """
    starting_directory = Path(os.path.abspath(directory))

    if not overrides:
        overrides = []
    overrides = [rule_from_pattern(
        p, str(starting_directory), source=('manual override', None)
    ) for p in overrides]

    # Git is incapable of adding .git files to stage -- by default walk()
    # will skip it entirely in a *non-overrideable* manner.
    if ignore_completely is None:
        ignore_completely = ['.git']
    elif not ignore_completely:
        ignore_completely = []
    ignore_completely = [
        rule_from_pattern(p, source=('application-level override', None))
        for p in ignore_completely
    ]
    if [rule for rule in ignore_completely if rule.negation]:
        raise ValueError('negation rules are not allowed in the '
                         'ignore completely rules')

    # Rule list will be a dict, keyed by directory with each value a
    # possibly-empty list of IgnoreRules
    rule_list = {}
    while True:
        for root, dirs, files in _walk(directory, onerror=onerror):
            rules = []
            if filename in files:
                rules.extend(rules_from_file(filename, os.path.abspath(root)))
            current_dir = Path(os.path.abspath(root))
            rel_path = str(current_dir.relative_to(starting_directory))
            rule_list[rel_path] = rules
            # Now, make a list of rules, working our way back to the
            # base directory.
            applicable_rules = [rule_list[rel_path]]
            if root != directory:
                for p in Path(root).parents:
                    rel_parent = str(p.relative_to(starting_directory))
                    applicable_rules.append(rule_list[rel_parent])
                    if p not in starting_directory.parents:
                        break
            applicable_rules.append(rule_list['.'])
            # Our rules are actually ordered from the base down
            applicable_rules = applicable_rules[::-1]
            flat_list = list(
                itertools.chain.from_iterable(applicable_rules)
            )
            # overrides and ignore-completely patterns are always applicable
            flat_list.extend(overrides)
            flat_list.extend(ignore_completely)
            ignore = []
            for d in dirs:
                included = True
                path = os.path.abspath(os.path.join(root, d))
                for rule in flat_list:
                    if included != rule.negation:
                        if rule.match(path):
                            included = not included
                if not included:
                    ignore.append(d)
            dirs[:] = [d for d in dirs if d not in ignore]
            ignore = []
            for f in files:
                included = True
                path = os.path.join(root, f)
                for rule in flat_list:
                    if rule.directory_only:
                        continue
                    if included != rule.negation:
                        if rule.match(os.path.abspath(path)):
                            included = not included
                if not included:
                    ignore.append(f)
            files[:] = [f for f in files if f not in ignore]
            yield root, dirs, files
        return


def rules_from_file(filename, base_path):
    return_rules = []
    full_path = os.path.join(base_path, filename)
    with open(full_path) as ignore_file:
        counter = 0
        for line in ignore_file:
            counter += 1
            line = line.rstrip('\n')
            rule = rule_from_pattern(line, os.path.abspath(base_path),
                                     source=(full_path, counter))
            if rule:
                return_rules.append(rule)
    return return_rules


def rule_from_pattern(pattern, base_path=None, source=None):
    """
    Take a .gitignore match pattern, such as "*.py[cod]" or "**/*.bak",
    and return an IgnoreRule suitable for matching against files and
    directories. Patterns which do not match files, such as comments
    and blank lines, will return None.

    Because git allows for nested .gitignore files, a base_path value
    is required for correct behavior. The base path should be absolute.
    """
    if base_path and base_path != os.path.abspath(base_path):
        raise ValueError('base_path must be absolute')
    # Store the exact pattern for our repr and string functions
    orig_pattern = pattern
    # Early returns follow
    # Discard comments and seperators
    if pattern.strip() == '' or pattern[0] == '#':
        return
    # Discard anything with more than two consecutive asterisks
    if pattern.find('***') > -1:
        return
    # Strip leading bang before examining double asterisks
    if pattern[0] == '!':
        negation = True
        pattern = pattern[1:]
    else:
        negation = False
    # Discard anything with invalid double-asterisks -- they can appear
    # at the start or the end, or be surrounded by slashes
    for m in re.finditer(r'\*\*', pattern):
        start_index = m.start()
        if (start_index != 0 and start_index != len(pattern) - 2 and
                (pattern[start_index - 1] != '/' or
                 pattern[start_index + 2] != '/')):
            return

    # Special-casing '/', which doesn't match any files or directories
    if pattern.rstrip() == '/':
        return

    directory_only = pattern[-1] == '/'
    # A slash is a sign that we're tied to the base_path of our rule
    # set.
    anchored = '/' in pattern[:-1]
    if pattern[0] == '/':
        pattern = pattern[1:]
    if pattern[0] == '*' and pattern[1] == '*':
        pattern = pattern[2:]
        anchored = False
    if pattern[0] == '/':
        pattern = pattern[1:]
    if pattern[-1] == '/':
        pattern = pattern[:-1]
    regex = utils.fnmatch_pathname_to_regex(
        pattern
    )
    if anchored:
        regex = ''.join(['^', regex])
    return utils.IgnoreRule(
        pattern=orig_pattern,
        regex=regex,
        negation=negation,
        directory_only=directory_only,
        anchored=anchored,
        base_path=Path(base_path) if base_path else None,
        source=source
    )
