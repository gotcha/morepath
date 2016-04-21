"""Implementation of routing.

The idea is to turn the routes into a tree, so that the routes::

  a/b
  a/b/c
  a/d

become a tree like this::

  a
    b
    b
      c
    d

Nodes in the tree can have a value attached that can be found through
routing; in Morepath the value is a model instance factory.

When presented with a path, Traject traverses this internal tree.

For a description of a similar algorithm also read: http://littledev.nl/?p=99
"""

import posixpath
import re
from functools import total_ordering

from .converter import IDENTITY_CONVERTER
from .error import TrajectError


IDENTIFIER = re.compile(r'^[^\d\W]\w*$')
"""regex for a valid variable name in a route.

same rule as for Python identifiers.
"""

PATH_VARIABLE = re.compile(r'\{([^}]*)\}')
"""regex to find curly-brace marked variables ``{foo}`` in routes.
"""

PATH_SEPARATOR = re.compile(r'/+')
"""regex for path separator, a ``/`` or ``/`` repeated.
"""


@total_ordering
class Step(object):
    """A single step in the tree.

    :param s: the path segment, such as ``'foo'`` or ``'{variable}'`` or
      ``'foo{variable}bar'``.
    :param converters: dict of converters for variables.
    """
    def __init__(self, s, converters=None):
        self.s = s
        self.converters = converters or {}
        self.generalized = generalize_variables(s)
        self.parts = tuple(self.generalized.split('{}'))
        self._variables_re = create_variables_re(s)
        self.names = parse_variables(s)
        self.cmp_converters = [self.get_converter(name) for name in self.names]
        self.validate()
        self.named_interpolation_str = interpolation_str(s) % tuple(
            [('%(' + name + ')s') for name in self.names])
        if len(set(self.names)) != len(self.names):
            raise TrajectError("Duplicate variable")

    def validate(self):
        """Validate whether step makes sense.

        Raises :class:`morepath.error.TrajectError` if there is a problem
        with the segment.
        """
        self.validate_parts()
        self.validate_variables()

    def validate_parts(self):
        """Check whether all non-variable parts of the segment are valid.

        Raises :class:`morepath.error.TrajectError` if there is a problem
        with the segment.
        """
        # XXX should also check for valid URL characters
        for part in self.parts:
            if '{' in part or '}' in part:
                raise TrajectError("invalid step: %s" % self.s)

    def validate_variables(self):
        """Check whether all variables of the segment are valid.

        Raises :class:`morepath.error.TrajectError` if there is a problem
        with the variables.
        """
        parts = self.parts
        if parts[0] == '':
            parts = parts[1:]
        if parts[-1] == '':
            parts = parts[:-1]
        for part in parts:
            if part == '':
                raise TrajectError(
                    "illegal consecutive variables: %s" % self.s)

    def discriminator_info(self):
        """Information needed to construct path discriminator.
        """
        return self.generalized

    def has_variables(self):
        """True if there are any variables in this step.
        """
        return bool(self.names)

    def match(self, s):
        """Match this step with actual path segment.

        :param s: path segment to match with
        :return: (bool, variables) tuple. The bool indicates whether
          ``s`` with the step or not. ``variables`` is a dictionary that
          contains converted variables that matched in this segment.
        """
        result = {}
        matched = self._variables_re.match(s)
        if matched is None:
            return False, result
        for name, value in zip(self.names, matched.groups()):
            converter = self.get_converter(name)
            try:
                result[name] = converter.decode([value])
            except ValueError:
                return False, {}
        return True, result

    def get_converter(self, name):
        """Get converter for a variable name.

        If no converter is listed explicitly, do no conversion.
        """
        return self.converters.get(name, IDENTITY_CONVERTER)

    def __eq__(self, other):
        """True if this step is the same as another.
        """
        if self.s != other.s:
            return False
        return self.cmp_converters == other.cmp_converters

    def __ne__(self, other):
        """True if this step is not equal to another.
        """
        if self.s != other.s:
            return True
        return self.cmp_converters != other.cmp_converters

    def __lt__(self, other):
        """Used for inserting steps in correct place in the tree.

        The order in which a step is inserted into the tree compared
        to its siblings affects which step preferentially matches first.

        In Traject, steps that contain no variables match before steps
        that do contain variables. Steps with more specific variables
        sort before steps with more general ones, i.e. ``prefix{foo}`` sorts
        before ``{foo}`` as ``prefix{foo}`` is more specific.
        """
        # if we have the same non-variable parts, we sort after the other
        # but this should generally be a conflict
        if self.parts == other.parts:
            return False
        # if we can absorb the other's variables we sort after it,
        # we'd have less hardcoded and more variables
        if self._variables_re.match(other.s) is not None:
            return False
        # we sort before other if other's variables can absorb us,
        # this means we have less variables and more hardcoded.
        if other._variables_re.match(self.s) is not None:
            return True
        # sort by non-variable parts alphabetically
        return self.parts > other.parts


class Node(object):
    """A node in the traject tree.
    """
    def __init__(self):
        self._name_nodes = {}
        self._variable_nodes = []
        self.value = None
        self.absorb = False

    def add(self, step):
        """Add a step into the tree as a child node of this node.
        """
        if not step.has_variables():
            return self.add_name_node(step)
        return self.add_variable_node(step)

    def add_name_node(self, step):
        """Add a step into the tree as a node that doesn't match variables.
        """
        node = self._name_nodes.get(step.s)
        if node is not None:
            return node
        node = StepNode(step)
        self._name_nodes[step.s] = node
        return node

    def add_variable_node(self, step):
        """Add a step into the tree as a node that matches variables.
        """
        for i, node in enumerate(self._variable_nodes):
            if node.step == step:
                return node
            if node.step.generalized == step.generalized:
                raise TrajectError("step %s and %s are in conflict" %
                                   (node.step.s, step.s))
            if step > node.step:
                continue
            result = StepNode(step)
            self._variable_nodes.insert(i, result)
            return result
        result = StepNode(step)
        self._variable_nodes.append(result)
        return result

    def get(self, segment):
        """Match a path segment, traversing this node.

        Matches non-variable nodes before nodes with variables in them.

        :segment: a path segment
        :return: a (bool, variables) tuple. Bool is ``True`` if
          matched, ``variables`` is a dictionary with matched variables.
        """
        node = self._name_nodes.get(segment)
        if node is not None:
            return node, {}
        for node in self._variable_nodes:
            matched, variables = node.match(segment)
            if matched:
                return node, variables
        return None, {}


class StepNode(Node):
    """A node that is also a step in that it can match.

    :param step: the step
    """
    def __init__(self, step):
        super(StepNode, self).__init__()
        self.step = step

    def match(self, segment):
        """Match a segment with the step.
        """
        return self.step.match(segment)


class Path(object):
    """Helper when registering paths.

    Used by :meth:`morepath.App.path` to register inverse paths used for
    link generation.

    Also used by :meth:`morepath.App.path` for creating discriminators.

    :param path: the route.
    """
    def __init__(self, path):
        self.path = path
        self.steps = [Step(segment) for segment in reversed(parse_path(path))]

    def discriminator(self):
        """Creates a unique discriminator for the path.
        """
        return '/'.join([step.discriminator_info() for step in self.steps])

    def interpolation_str(self):
        """Create a string for interpolating variables.

        Used for link generation (inverse).
        """
        return '/'.join([step.named_interpolation_str for step in self.steps])

    def variables(self):
        """Get the variables used by the path.

        :return: a list of variable names
        """
        result = []
        for step in self.steps:
            result.extend(step.names)
        return set(result)


class TrajectRegistry(object):
    """Tree of route steps.
    """
    def __init__(self):
        self._root = Node()

    def add_pattern(self, path, value, converters=None, absorb=False):
        """Add a route to the tree.

        :path: route to add.
        :value: the value to store for the end step of the route.
        :converters: converters to store with the end step of the route
        :absorb: does this path absorb all segments
        """
        node = self._root
        known_variables = set()
        for segment in reversed(parse_path(path)):
            step = Step(segment, converters)
            node = node.add(step)
            variables = set(step.names)
            if known_variables.intersection(variables):
                raise TrajectError("Duplicate variables")
            known_variables.update(variables)
        node.value = value
        if absorb:
            node.absorb = True

    def consume(self, stack):
        """Consume a stack given routes.

        :param stack: the stack of segments on a path, reversed so that
          the first segment of the path is on top.
        :return: ``value, stack, variables`` tuple: ``value`` is the
          value registered with the deepest node that matched, ``stack``
          is the remaining segment stack and ``variables`` are the variables
          matched with the segments.
        """
        stack = stack[:]
        node = self._root
        variables = {}
        while stack:
            if node.absorb:
                variables['absorb'] = '/'.join(reversed(stack))
                return node.value, [], variables
            segment = stack.pop()
            # special view prefix
            if segment.startswith('+'):
                stack.append(segment)
                return node.value, stack, variables
            new_node, new_variables = node.get(segment)
            if new_node is None:
                stack.append(segment)
                return node.value, stack, variables
            node = new_node
            variables.update(new_variables)
        if node.absorb:
            variables['absorb'] = ''
            return node.value, stack, variables
        return node.value, stack, variables


def parse_path(path):
    """Parse a path /foo/bar/baz to a stack of steps.

    A step is a string, such as 'foo', 'bar' and 'baz'.

    :param path: the path string
    :return: a stack of steps, first segment on top
    """

    # make sure dots are normalized away (may leave a single dot -> '.')
    path = posixpath.normpath(path).strip('/')

    if not path or path == '.':
        return []

    result = PATH_SEPARATOR.split(path)
    result.reverse()
    return result


def create_path(stack):
    """Builds a path from a stack.

    :param stack: stack of steps, first segment on top
    :return: a path
    """
    return '/' + u'/'.join(reversed(stack))


def normalize_path(path):
    """Normalizes the path as follows:

    * Collapses dots (``/../blog`` -> ``/blog``)
    * Ensures absolute paths (``./site`` -> ``/site``)
    * Removes double-slashes (``//index`` -> ``/index``)

    For example:

        ``../static//../app.py`` is turned into ``/app.py``

    """
    # the path is always absolute
    path = path.lstrip('.')

    # normpath returns '.' instead of '' if the path is empty, we want '/'
    path = posixpath.normpath(path)
    return path if path != '.' else '/'


def is_identifier(s):
    """Check whether a variable name is a proper identifier.

    :param s: variable
    :return: True if variable is an identifier.
    """
    return IDENTIFIER.match(s) is not None


def parse_variables(s):
    """Parse variables out of a segment.

    Raised a :class:`morepath.error.TrajectError`` if a variable
    is not a valid identifier.

    :param s: a path segment
    :return: a list of variables.
    """
    result = PATH_VARIABLE.findall(s)
    for name in result:
        if not is_identifier(name):
            raise TrajectError(
                "illegal variable identifier: %s" % name)
    return result


def create_variables_re(s):
    """Create regular expression that matches variables from route segment.

    :param s: a route segment with variables in it.
    :return: a regular expression that matches with variables for the route.
    """
    return re.compile('^' + PATH_VARIABLE.sub(r'(.+)', s) + '$')


def generalize_variables(s):
    """Generalize a route segment.

    :param s: a route segment.
    :return: a generalized route where all variables are empty ({}).
    """
    return PATH_VARIABLE.sub('{}', s)


def interpolation_str(s):
    """Create a Python string with interpolation variables for a route segment.

    Given ``a{foo}b``, creates ``a%sb``.
    """
    return PATH_VARIABLE.sub('%s', s)
