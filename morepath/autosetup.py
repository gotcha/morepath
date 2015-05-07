import importlib
import pkg_resources
from morepath.core import setup


def autoconfig(ignore=None):
    """Automatically load Morepath configuration from packages.

    Morepath configuration consists of decorator calls on :class:`App`
    instances, i.e. ``@App.view()`` and ``@App.path()``.

    This function tries to load needed Morepath configuration from all
    packages automatically. This only works if:

    * The package is made available using a ``setup.py`` file.

    * The package includes ``morepath`` in the ``install_requires`` list of
    the ``setup.py`` file.

    * The package is named like the module it contains. For example: if the
    module inside the package is named 'myapp', the package must be named
    'myapp' as well (not ``my-app`` or ``MyApp``).

    This function creates a :class:`Config` object as with :func:`setup`, but
    before returning it scans all packages, looking for those that depend on
    Morepath directly or indirectly. This includes the package that
    calls this function. Those packages are then scanned for
    configuration as with :meth:`Config.scan`.

    You can add manual :meth:`Config.scan` calls yourself on the
    returned :class:`Config` object. Finally you need to call
    :meth:`Config.commit` on the returned :class:`Config` object so
    the configuration is committed.

    Typically called immediately after startup just before the
    application starts serving using WSGI.

    ``autoconfig`` always ignores ``.test`` and ``.tests``
    sub-packages -- these are assumed never to contain useful Morepath
    configuration and are not scanned.

    ``autoconfig`` can fail with an ``ImportError`` when it tries to
    scan code that imports an optional dependency that is not
    installed. This happens most commonly in test code, which often
    rely on test-only dependencies such as ``pytest`` or ``nose``. If
    those tests are in a ``.test`` or ``.tests`` sub-package they
    are automatically ignored, however.

    If you have a special package with such expected import errors,
    you can exclude them from ``autoconfig`` using the ``ignore``
    argument, for instance using ``['special_package']``. You then can
    use :class:`Config.scan` for that package, with a custom
    ``ignore`` argument that excludes the modules that generate import
    errors.

    See also :func:`autosetup`.

    :param ignore: Venusian_ style ignore to ignore some modules
      during scanning. Optional. If ommitted, ignore ``.test`` and
      ``.tests`` packages by default.
    :returns: :class:`Config` object.

    .. _Venusian: http://venusian.readthedocs.org

    """
    if ignore is None:
        ignore = []
        ignore.extend(['.test', '.tests'])
    c = setup()
    for package in morepath_packages():
        c.scan(package, ignore)
    return c


def autosetup(ignore=None):
    """Automatically commit Morepath configuration from packages.

    As with :func:`autoconfig`, but also commits
    configuration. This can be your one-stop function to load all
    Morepath configuration automatically.

    Typically called immediately after startup just before the
    application starts serving using WSGI.

    ``autosetup`` always ignores ``.test`` and ``.tests``
    sub-packages -- these are assumed never to contain useful Morepath
    configuration and are not scanned.

    ``autosetup`` can fail with an ``ImportError`` when it tries to
    scan code that imports an optional dependency that is not
    installed. This happens most commonly in test code, which often
    rely on test-only dependencies such as ``pytest`` or ``nose``. If
    those tests are in a ``.test`` or ``.tests`` sub-package they
    are automatically ignored, however.

    If you have a special package with such expected import errors,
    you may be better off switching to :func:`morepath.autoconfig`
    with an ignore for this package, and then doing a manual
    :class:`Config.scan` for that package with the resulting config
    object. There you can add a custom ``ignore`` argument that
    excludes the modules that generate import errors.

    :param ignore: Venusian_ style ignore to ignore some modules
      during scanning. Optional. If ommitted, ignore ``.test`` and
      ``.tests`` by default.
    """
    c = autoconfig(ignore)
    c.commit()


class DependencyMap(object):
    def __init__(self):
        self._d = {}
        self._dists = {}

    def load(self):
        for dist in pkg_resources.working_set:
            self._dists[dist.project_name] = dist
            for r in dist.requires():
                self._d.setdefault(
                    dist.project_name, set()).add(r.project_name)

    def depends(self, project_name, on_project_name):
        dependent_project_names = self._d.get(project_name, set())
        if on_project_name in dependent_project_names:
            return True
        for n in dependent_project_names:
            if self.depends(n, on_project_name):
                return True
        return False

    def relevant_dists(self, on_project_name):
        for dist in pkg_resources.working_set:
            if not self.depends(dist.project_name, on_project_name):
                continue
            yield dist


def morepath_packages():
    """ Yields modules that depend on morepath. Each such module is
    imported before it is returned.

    If the name of the package differs from the name of its module, the
    import will fail. See :func:`autoconfig` for more information.

    """
    m = DependencyMap()
    m.load()

    for dist in m.relevant_dists('morepath'):
        yield importlib.import_module(dist.project_name)
