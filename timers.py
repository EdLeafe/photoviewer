from threading import Timer


class RepeatingTimer():
   def __init__(self, interval, function):
      self.interval = interval
      self.function = function
      self.thread = Timer(self.interval, self.handle_function)

   def handle_function(self):
      self.function()
      self.thread = Timer(self.interval, self.handle_function)
      self.thread.start()

   def start(self):
      self.thread.start()

   def cancel(self):
      self.thread.cancel()



if __name__ == "__main__":

    def printer1():
        print 'timer 1'

    def printer2():
        print 'timer 2'

    t1 = RepeatingTimer(3, printer1)
    t2 = RepeatingTimer(5, printer2)
    print "starting t1"
    t1.start()
    print "starting t2"
    t2.start()
