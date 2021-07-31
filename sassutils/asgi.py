""":mod:`sassutils.asgi` --- ASGI middleware for development purpose

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
"""
from __future__ import absolute_import

import logging
import os
import os.path

from pkg_resources import resource_filename

from sass import CompileError
from sassutils._compat import collections_abc
from sassutils.builder import Manifest

__all__ = ("SassMiddleware",)


class SassMiddleware(object):
    """ASGI middleware for development purpose.  Every time a CSS file has
    requested it finds a matched Sass/SCSS source file and then compiled
    it into CSS.

    It shows syntax errors in three ways:
    Heading comment
        The result CSS includes detailed error message in the heading
        CSS comment e.g.:
        .. code-block:: css
            /*
            Error: invalid property name
            */
    Red text in ``body:before``
        The result CSS draws detailed error message in ``:before``
        pseudo-class of ``body`` element e.g.:
        .. code-block:: css
            body:before {
                content: 'Error: invalid property name';
                color: maroon;
                background-color: white;
            }
        In most cases you could be aware of syntax error by refreshing your
        working document because it will removes all other styles and leaves
        only a red text.

    :mod:`logging`
        It logs syntax errors if exist during compilation to
        ``sassutils.asgi.SassMiddleware`` logger with level ``ERROR``.
        To enable this::

            from logging import Formatter, StreamHandler, getLogger
            logger = getLogger('sassutils.asgi.SassMiddleware')
            handler = StreamHandler(level=logging.ERROR)
            formatter = Formatter(fmt='*' * 80 + '\n%(message)s\n' + '*' * 80)
            handler.setFormatter(formatter)
            logger.addHandler(handler)

        Or simply::

            import logging
            logging.basicConfig()

    :param app: the ASGI application to wrap
    :type app: :class:`collections.abc.Callable`
    :param manifests: build settings.  the same format to
                      :file:`setup.py` script's ``sass_manifests``
                      option
    :type manifests: :class:`collections.abc.Mapping`
    :param package_dir: optional mapping of package names to directories.
                        the same format to :file:`setup.py` script's
                        ``package_dir`` option
    :type package_dir: :class:`collections.abc.Mapping`
    .. versionchanged:: 0.4.0
       It creates also source map files with filenames followed by
       :file:`.map` suffix.
    .. versionadded:: 0.8.0
       It logs syntax errors if exist during compilation to
       ``sassutils.asgi.SassMiddleware`` logger with level ``ERROR``.
    """

    def __init__(
        self,
        app,
        manifests,
        package_dir={},
        error_status="200 OK",
    ):

        if not callable(app):
            raise TypeError(
                "app must be a ASGI-compliant callable object, not "
                + repr(app),
            )
        self.app = app
        self.manifests = Manifest.normalize_manifests(manifests)
        if not isinstance(package_dir, collections_abc.Mapping):
            raise TypeError(
                "package_dir must be a mapping object, not "
                + repr(package_dir),
            )
        self.error_status = error_status
        self.package_dir = dict(package_dir)
        for package_name in self.manifests:
            if package_name in self.package_dir:
                continue
            path = resource_filename(package_name, "")
            self.package_dir[package_name] = path
        self.paths = []
        for package_name, manifest in self.manifests.items():
            sgi_path = manifest.sgi_path
            if not sgi_path.startswith("/"):
                sgi_path = "/" + sgi_path
            if not sgi_path.endswith("/"):
                sgi_path += "/"
            package_dir = self.package_dir[package_name]
            self.paths.append((sgi_path, package_dir, manifest))

    async def __call__(
        self,
        scope,
        receive,
        send,
    ):
        """
            scope: Scope,
            receive: ASGIReceiveCallable,
            send: ASGISendCallable
        """
        path = scope.get("path", "/")
        if path.endswith(".css"):
            for prefix, package_dir, manifest in self.paths:
                if not path.startswith(prefix):
                    continue
                css_filename = path[len(prefix):]
                sass_filename = manifest.unresolve_filename(
                    package_dir,
                    css_filename,
                )
                try:
                    result = manifest.build_one(
                        package_dir,
                        sass_filename,
                        source_map=True,
                    )
                except (IOError, OSError):
                    break

                except CompileError as e:
                    logger = logging.getLogger(__name__ + ".SassMiddleware")
                    logger.error(str(e))
                    await send(
                        {
                            "type": "http.response.start",
                            "status": self.error_status,
                            "headers": [
                                ("Content-Type", "text/css; charset=utf-8"),
                            ],
                        },
                    )
                    return [
                        b"/*\n",
                        str(e).encode("utf-8"),
                        b"\n*/\n\n",
                        b"body:before { content: ",
                        self.quote_css_string(str(e)).encode("utf-8"),
                        b"; color: maroon; background-color: white",
                        b"; white-space: pre-wrap; display: block",
                        b'; font-family: "Courier New", monospace'
                        b"; user-select: text; }",
                    ]

                def read_file(path):
                    with open(path, 'rb') as in_:
                        while 1:
                            chunk = in_.read(4096)
                            if chunk:
                                yield chunk
                            else:
                                break
                await send(
                    {
                        "type": "http.response.start",
                        "status": "200",
                        "body": read_file(os.path.join(package_dir, result)),
                        "headers": [
                            ("Content-Type", "text/css; charset=utf-8"),
                        ],
                    },
                )
        return await self.app(scope, receive, send)

    @staticmethod
    def quote_css_string(s):
        """Quotes a string as CSS string literal."""
        return "'" + ''.join('\\%06x' % ord(c) for c in s) + "'"
