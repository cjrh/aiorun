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

So what the heck does ``run()`` do exactly?? It:

- creates a `Task` for the given coroutine (schedules it on the
  event loop),
- calls ``loop.run_forever()``,
- adds default (and smart) signal handlers for both ``SIGINT``
  and ``SIGTERM`` that will stop the loop, *and then it...*
- gathers all outstanding tasks,
- cancels them using ``task.cancel()`` (you can choose whether or
  not to handle ``CancelledError`` in your coroutines),
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

There's not much else to know.
