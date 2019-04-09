.. contents:: Table of Contents


.. image:: https://travis-ci.org/cjrh/aiorun.svg?branch=master
    :target: https://travis-ci.org/cjrh/aiorun

.. image:: https://ci.appveyor.com/api/projects/status/fov7fixihcokvifl/branch/master?svg=true
    :target: https://ci.appveyor.com/project/cjrh/aiorun

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


🏃 aiorun
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
of your ``asyncio``-based application. The ``run()`` function will
run forever. If you want to shut down when ``main()`` completes, just
call ``loop.stop()`` inside it: that will initiate shutdown.


🤔 Why?
----------------

The ``run()`` function will handle **everything** that normally needs
to be done during the shutdown sequence of the application.  All you
need to do is write your coroutines and run them.

So what the heck does ``run()`` do exactly?? It does these standard,
idiomatic actions for asyncio apps:

- creates a ``Task`` for the given coroutine (schedules it on the
  event loop),
- calls ``loop.run_forever()``,
- adds default (and smart) signal handlers for both ``SIGINT``
  and ``SIGTERM`` that will stop the loop;
- and *when* the loop stops (either by signal or called directly), then it will...
- ...gather all outstanding tasks,
- cancel them using ``task.cancel()``,
- resume running the loop until all those tasks are done,
- wait for the *executor* to complete shutdown, and
- finally close the loop.

All of this stuff is boilerplate that you will never have to write
again. So, if you use ``aiorun`` this is what **you** need to remember:

- Spawn all your work from a single, starting coroutine
- When a shutdown signal is received, **all** currently-pending tasks
  will have ``CancelledError`` raised internally. It's up to you whether
  you want to handle this inside each coroutine with
  a ``try/except`` or not.
- If you want to protect coros from cancellation, see `shutdown_waits_for()`
  further down.
- Try to have executor jobs be shortish, since the shutdown process will wait
  for them to finish. If you need a long-running thread or process tasks, use
  a dedicated thread/subprocess and set ``daemon=True`` instead.

There's not much else to know for general use. `aiorun` has a few special
tools that you might need in unusual circumstances. These are discussed
next.

🖥️ What about TCP server startup?
-----------------------------------

You will see in many examples online that for servers, startup happens in
several ``run_until_complete()`` phases before the primary ``run_forever()``
which is the "main" running part of the program. How do we handle that with
*aiorun*?

Let's recreate the `echo client & server <https://docs.python.org/3/library/asyncio-stream.html#tcp-echo-client-using-streams>`_
examples from the Standard Library documentation:

**Client:**

.. code-block:: python

    # echo_client.py
    import asyncio
    from aiorun import run

    async def tcp_echo_client(message):
        # Same as original!
        reader, writer = await asyncio.open_connection('127.0.0.1', 8888)
        print('Send: %r' % message)
        writer.write(message.encode())
        data = await reader.read(100)
        print('Received: %r' % data.decode())
        print('Close the socket')
        writer.close()
        asyncio.get_event_loop().stop()  # Exit after one msg like original

    message = 'Hello World!'
    run(tcp_echo_client(message))

**Server:**

.. code-block:: python

    import asyncio
    from aiorun import run

    async def handle_echo(reader, writer):
        # Same as original!
        data = await reader.read(100)
        message = data.decode()
        addr = writer.get_extra_info('peername')
        print("Received %r from %r" % (message, addr))
        print("Send: %r" % message)
        writer.write(data)
        await writer.drain()
        print("Close the client socket")
        writer.close()

    async def main():
        server = await asyncio.start_server(handle_echo, '127.0.0.1', 8888)
        print('Serving on {}'.format(server.sockets[0].getsockname()))
        try:
            # Wait for cancellation
            while True:
                await asyncio.sleep(10)
        except asyncio.CancelledError:
            server.close()
            await server.wait_closed()

    run(main())

It works the same as the original examples, except you see this
when you hit ``CTRL-C`` on the server instance:

.. code-block:: bash

    $ python echo_server.py
    Running forever.
    Serving on ('127.0.0.1', 8888)
    Received 'Hello World!' from ('127.0.0.1', 57198)
    Send: 'Hello World!'
    Close the client socket
    ^CStopping the loop
    Entering shutdown phase.
    Cancelling pending tasks.
    Cancelling task:  <Task pending coro=[...snip...]>
    Running pending tasks till complete
    Waiting for executor shutdown.
    Leaving. Bye!

Task gathering, cancellation, and executor shutdown all happen
automatically.

💨 Do you like `uvloop <https://github.com/magicstack/uvloop>`_?
------------------------------------------------------------------

.. code-block:: python

   import asyncio, aiorun

   async def main():
       <snip>

   if __name__ == '__main__':
       run(main(), use_uvloop=True)

Note that you have to ``pip install uvloop`` yourself.

🛡️ Smart shield for shutdown
---------------------------------

It's unusual, but sometimes you're going to want a coroutine to not get
interrupted by cancellation *during the shutdown sequence*. You'll look in
the official docs and find ``asyncio.shield()``.

Unfortunately, ``shield()`` doesn't work in shutdown scenarios because
the protection offered by ``shield()`` only applies if the specific coroutine
*inside which* the ``shield()`` is used, gets cancelled directly.

Let me explain: if you do a conventional shutdown sequence (like ``aiorun``
is doing internally), this is the sequence of steps:

- ``tasks = all_tasks()``, followed by
- ``group = gather(*tasks)``, and then
- ``group.cancel()``

The way ``shield()`` works internally is it creates a *secret, inner*
task—which also gets included in the ``all_tasks()`` call above! Thus
it also receives a cancellation signal just like everything else.

Therefore, we have an alternative version of ``shield()`` that works better for
us: ``shutdown_waits_for()``. If you've got a coroutine that must **not** be
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

If you hit ``CTRL-C`` *before* 60 seconds has passed, you will see
``oh noes!`` printed immediately, and then after 60 seconds (since start),
``done!`` is printed, and thereafter the program exits.

Behind the scenes, ``all_tasks()`` would have been cancelled by ``CTRL-C``,
*except* ones wrapped in ``shutdown_waits_for()`` calls.  In this respect, it
is loosely similar to ``asyncio.shield()``, but with special applicability
to our shutdown scenario in ``aiorun()``.

Be careful with this: the coroutine should still finish up at some point.
The main use case for this is short-lived tasks that you don't want to
write explicit cancellation handling.

Oh, and you can use ``shutdown_waits_for()`` as if it were ``asyncio.shield()``
too. For that use-case it works the same.  If you're using ``aiorun``, there
is no reason to use ``shield()``.

🙏 Windows Support
-------------------------

``aiorun`` also supports Windows! Kinda. Sorta. The root problem with Windows,
for a thing like ``aiorun`` is that Windows doesn't support *signal handling*
the way Linux or Mac OS X does. Like, at all.

For Linux, ``aiorun`` does "the right thing" out of the box for the
``SIGINT`` and ``SIGTERM`` signals; i.e., it will catch them and initiate
a safe shutdown process as described earlier. However, on *Windows*, these
signals don't work.

There are two signals that work on Windows: the ``CTRL-C`` signal (happens
when you press, unsurprisingly, ``CTRL-C``, and the ``CTRL-BREAK`` signal
which happens when you...well, you get the picture.

The good news is that, for ``aiorun``, both of these will work. Yay! The bad
news is that for them to work, you have to run your code in a Console
window. Boo!

Fortunately, it turns out that you can run an asyncio-based process *not*
attached to a Console window, e.g. as a service or a subprocess, *and* have
it also receive a signal to safely shut down in a controlled way. It turns
out that it is possible to *send a ``CTRL-BREAK`` signal* to another process,
with no console window involved, but only as long as that process was created
in a particular way and---here is the drop---this targetted process is a
child process of the one sending the signal. Yeah, I know, it's a downer.

There is an example of how to do this in the tests:

.. code-block:: python3

    import subprocess as sp

    proc = sp.Popen(
        ['python', 'app.py'],
        stdout=sp.PIPE,
        stderr=sp.STDOUT,
        creationflags=sp.CREATE_NEW_PROCESS_GROUP
    )
    print(proc.pid)

Notice how we print out the process id (``pid``). Then you can send that
process the signal from a completely different process, once you know
the ``pid``:

.. code-block:: python3

    import os, signal

    os.kill(pid, signal.CTRL_BREAK_EVENT)

(Remember, ``os.kill()`` doesn't actually kill, it only sends a signal)

``aiorun`` supports this use-case above, although I'll be pretty surprised
if anyone actually uses it to manage microservices (does anyone do this?)

So to summarize: ``aiorun`` will do a controlled shutdown if either
``CTRL-C`` or ``CTRL-BREAK`` is entered via keyboard in a Console window
with a running instance, or if the ``CTRL-BREAK`` signal is sent to
a *subprocess* that was created with the ``CREATE_NEW_PROCESS_GROUP``
flag set. `Here <https://stackoverflow.com/a/35792192>`_ is a much more
detailed explanation of these issues.

Finally, ``uvloop`` is not yet supported on Windows so that won't work
either.

At the very least, ``aiorun`` will, well, *run* on Windows ¯\_(ツ)_/¯
