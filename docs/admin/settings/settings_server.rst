.. _settings server:

===========
``server:``
===========

.. code:: yaml

   server:
       base_url: http://example.org/location  # change this!
       port: 8888
       bind_address: "127.0.0.1"
       secret_key: "ultrasecretkey"           # change this!
       limiter: false
       public_instance: false
       image_proxy: false
       method: "GET"
       default_http_headers:
         X-Content-Type-Options : nosniff
         X-Download-Options : noopen
         X-Robots-Tag : noindex, nofollow
         Referrer-Policy : no-referrer

``base_url`` : ``$SEARXNG_BASE_URL``
  The base URL where SearXNG is deployed.  Used to create correct inbound links.

``port`` & ``bind_address``: ``$SEARXNG_PORT`` & ``$SEARXNG_BIND_ADDRESS``
  Port number and *bind address* of the SearXNG web application if you run it
  directly using ``python searx/webapp.py``.  Doesn't apply to a SearXNG
  services running behind a proxy and using socket communications.

.. _server.secret_key:

``secret_key`` : ``$SEARXNG_SECRET``
  Used for cryptography purpose.

``limiter`` :  ``$SEARXNG_LIMITER``
  Rate limit the number of request on the instance, block some bots.  The
  :ref:`limiter` requires a :ref:`settings valkey` database.

.. _public_instance:

``public_instance`` :  ``$SEARXNG_PUBLIC_INSTANCE``

  Setting that allows to enable features specifically for public instances (not
  needed for local usage).  By set to ``true`` the following features are
  activated:

  - :py:obj:`searx.botdetection.link_token` in the :ref:`limiter`
  - :ref:`image_proxy`

.. _image_proxy:

``image_proxy`` : ``$SEARXNG_IMAGE_PROXY``
  Allow your instance of SearXNG of being able to proxy images.  Uses memory space.

.. _method:

``method`` : ``GET`` | ``POST``

  HTTP method.  By default, ``GET`` is used / The ``POST`` method has the
  advantage with some browsers that the history is not saved, but
  there are also various disadvantages that sometimes **severely restrict the
  ease of use for the end user** (e.g. back button to jump back to the previous
  search page and drag & drop of search term to new tabs do not work as
  expected .. and several more).  We had some discussions about the *pros
  versus cons*:

  - `[doc] adds the missing documentation of the server.method settings
    <https://github.com/searxng/searxng/pull/3619>`__
  - look out for `label:"http methods GET & POST"
    <https://github.com/search?q=repo%3Asearxng%2Fsearxng+label%3A%22http+methods+GET+%26+POST%22>`__

.. _HTTP headers: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers

``default_http_headers`` :
  Set additional `HTTP headers`_, see `#755 <https://github.com/searx/searx/issues/715>`__

.. _server.rate_limit:

``rate_limit`` :
  Configuration of the rate limiter of the API.  The limiter counts the
  requests of a client in a fixed window of one minute and rejects the
  requests above the limit with a ``429``.  The counters are kept in a
  :ref:`settings valkey` database, only a hash of the client IP is stored
  (never the IP itself).

  The limiter is **opt-in**: it is only registered when ``enabled`` is
  ``true``.  Instances that do not enable it do not pay the cost of the
  middleware at all.  This is the recommended setup for the vast majority of
  the instances, which are small and local.

  .. code:: yaml

     server:
       rate_limit:
         enabled: false
         requests_per_minute: 30
         paths:
           - /api/v1/search
         trusted_proxies: []
         secret: null

  ``enabled`` : ``$SEARXNG_RATE_LIMIT_ENABLED``
    Activate the rate limiter.

  ``requests_per_minute`` : ``$SEARXNG_RATE_LIMIT_RPM``
    Number of requests a client may send per minute.

  ``paths``
    The routes that are rate limited.  A trailing slash in the path is
    ignored.

  ``trusted_proxies`` : ``$SEARXNG_RATE_LIMIT_TRUSTED_PROXIES``
    Networks of the reverse proxies in front of the instance, e.g.
    ``["127.0.0.1/32", "::1/128", "172.16.0.0/12"]``.  Only the
    ``X-Forwarded-For`` header of a request from a trusted proxy is
    evaluated, otherwise a client could spoof its IP.  If the instance runs
    behind a proxy that is not listed here, all clients share a single
    counter.

  ``secret``
    Secret to hash the client IP with.  Defaults to
    :ref:`server.secret_key`.  Use a strong random value: with a known
    secret the stored hashes can be reversed into IP addresses.
