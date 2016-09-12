from __future__ import print_function

import sched
import time

from photo import logit


class SPT(object):
    """Thanks to Alex Martelli for the basic idea for this class."""
    def __init__(self):
        logit("info", "Starting SPT")
        self._sched = sched.scheduler(time.time, time.sleep)
        self._timers = {}
        self._events = {}

    def add_timer(self, name, func, period, priority=0):
        is_new = name not in self._timers
        self._timers[name] = {"func": func, "period": period,
                "priority": priority}
        self._events[name] = None
        if is_new:
            self._events[name] = self._sched.enter(0, 0, self._action, (name,))
        return self._events[name]

    def start(self):
        for name, settings in self._timers.items():
            period = settings["period"]
            priority = settings["priority"]
            self._events[name] = self._sched.enter(period, priority,
                    self._action, (name, ))
        self._sched.run()
        logit("info", "SPT running")

    def _action(self, name):
        logit("info", "SPT _action called with:", name)
        timer = self._timers[name]
        period = timer["period"]
        priority = timer["priority"]
        func = timer["func"]
        self._events[name] = self._sched.enter(period, priority, self._action,
                (name, ))
        func()
        logit("info", "SPT queue:", self._sched.queue)

    def cancel(self, name):
        logit("info", "SPT cancel called with:", name)
        evt = self._events.pop(name)
        self._sched.cancel(evt)


if __name__ == "__main__":
    def f1():
        print("Function1", time.ctime())
    def f2():
        print("Function2", time.ctime())
    def f3():
        print("Function3", time.ctime())

    tim = SPT()
    evt = tim.add_timer("first", f1, 1, 99)
    tim.add_timer("second", f2, 2, 1)
    tim.add_timer("third", f3, 4)
    print(dir(evt))
    tim.start()
