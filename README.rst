.. image:: https://travis-ci.org/cjrh/aiorun.svg?branch=master
    :target: https://travis-ci.org/cjrh/aiorun

.. image:: https://coveralls.io/repos/github/cjrh/aiorun/badge.svg?branch=master
    :target: https://coveralls.io/github/cjrh/aiorun?branch=master

.. image:: https://img.shields.io/pypi/pyversions/aiorun.svg
    :target: https://pypi.python.org/pypi/aiorun

.. image:: https://img.shields.io/github/tag/cjrh/aiorun.svg
    :target: https://img.shields.io/github/tag/cjrh/aiorun.svg

.. image:: https://img.shields.io/badge/install-pip%20install%20aiorun-ff69b4.svg
    :target: https://img.shields.io/badge/install-pip%20install%20aiorun-ff69b4.svg

.. image:: https://img.shields.io/pypi/v/aiorun.svg
    :target: https://img.shields.io/pypi/v/aiorun.svg

.. image:: https://img.shields.io/badge/calver-YYYY.MM.MINOR-22bfda.svg
    :target: http://calver.org/

**ALPHA**

aiorun
======================

Here's the big idea (how you use it):

.. code-block:: python

   import asyncio
   from aiorun import run

   async def main():
       # Put your application code here
       await asyncio.sleep(1.0)

   if __name__ == '__main__':
       run(main())

This package provides a ``run()`` function as the starting point
of your ``asyncio``-based application.

The ``run()`` function will handle **everything** that normally needs
to be done during the shutdown sequence of the application.  All you
need to do is write your coroutines and run them.

So what the heck does ``run()`` do exactly?? It does these standard,
idiomatic actions for asyncio apps:

- creates a ``Task`` for the given coroutine (schedules it on the
  event loop),
- calls ``loop.run_forever()``,
- adds default (and smart) signal handlers for both ``SIGINT``
  and ``SIGTERM`` that will stop the loop, if if the loop stops it...
- ...gathers all outstanding tasks,
- cancels them using ``task.cancel()``,
- waits for the executor to complete shutdown, and
- finally closes the loop.

All of this stuff is boilerplate that you will never have to write
again. So, if you use ``aiorun`` this is what **you** need to remember:

- Spawn all your work from a single, starting coroutine
- When a shutdown signal is received, **all** currently-pending tasks
  will have ``CancelledError`` raised internally. It's up to you whether
  you want to handle this in a ``try/except`` or not.
- Try to have executor jobs be shortish, since shutdown will wait for them
  to finish. If you need a long-running thread or process tasks, use
  a dedicated thread/subprocess and set ``daemon=True`` instead.

There's not much else to know for general use. `aiorun` has a few special
tools that you might need in unusual circumstances. These are discussed
next.

Smart shield for shutdown
-------------------------

It's unusual, but sometimes you're going to want a coroutine to not get
interrupted by cancellation *during the shutdown sequence*. You'll look in
the official docs and find ``asyncio.shield()``.

The problem is that ``shield()`` doesn't work in shutdown scenarios because
the protection offered by ``shield()`` only applies if the specific coroutine
*inside which* the ``shield()`` is used, gets cancelled directly.

If, however, you go through a conventional shutdown sequence (like ``aiorun``
is doing internally), you would call ``tasks = all_tasks()``, followed by
``group = gather(*tasks)``, and then ``group.cancel()``, the *secret, inner*
task that ``shield()`` silently creates internally will get cancelled, since
it'll be includede in that ``all_tasks()`` call. It's not a very good shield
for the shutdown sequence.

Therefore, we have a version of ``shield()`` that works better for us:
``shutdown_waits_for()``. If you've got a coroutine that must **not** be
cancelled during the shutdown sequence, just wrap it in
``shutdown_waits_for()``!

Here's an example:

.. code-block:: python

    import asyncio
    from aiorun import run, shutdown_waits_for

    async def corofn():
        await asyncio.sleep(60)
        print('done!')

    async def main():
        try:
            await shutdown_waits_for(corofn())
        except asyncio.CancelledError
            print('oh noes!')

    run(main())

If you run this program, and do nothing, it'll run forever 'cause that's
how `aiorun.run()` works.  You will see only `done!` printed in the output,
and you'll have to send a signal or `CTRL-C` to stop it, at which point
you'll see `oh noes!` printed.

If, however, you hit `CTRL-C` *before* 60 seconds has passed, you will see
`oh noes!` printed immediately, and then after 60 seconds since start, `done!`
is printed, and thereafter the program exits.

Behind the scenes, *all tasks* would have been cancelled by `CTRL-C`,
except ones wrapped in `shutdown_waits_for()` calls.  In this respect, it
is loosely similar to `asyncio.shield()`, but with special applicability
to our shutdown scenario in `aiorun()`.

Oh, and you can use `shutdown_waits_for()` as if it were `asyncio.shield()`
too. For that use-case it works the same.  If you're using `aiorun`, there
is no reason to use `shield()`.
