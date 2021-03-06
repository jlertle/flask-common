import re

class SetCompare(object):
    """
    Comparator that doesn't take ordering into account. For example, the
    following expression is True:

    SetCompare([1, 2, 3]) == [2, 3, 1]
    """
    def __init__(self, members):
        self.members = members

    def __eq__(self, other):
        return set(other) == set(self.members)

class RegexSetCompare(object):
    """
    Comparator that takes a regex and a set of arguments and doesn't take
    ordering of the arguments into account. For example, the following
    expression is True:

    RegexSetCompare('(.*) OR (.*) OR (.*)', ['1', '2', '3']) == '2 OR 3 OR 1'
    """
    def __init__(self, regex, args):
        self.regex = re.compile(regex)
        self.args = args

    def __eq__(self, other):
        match = self.regex.match(other)
        if not match:
            return False
        return set(match.groups()) == set(self.args)

class Capture(object):
    """
    Comparator that always returns True and returns the captured object when
    called. For example:

    capture = Capture()
    capture == 'Hello'  # returns True
    capture()           # returns 'Hello'
    """
    def __call__(self):
        return self.obj

    def __eq__(self, other):
        self.obj = other
        return True
