void sink() {}
void choose(int x) { sink(); }
void choose(double x) {}
class Worker { public: Worker(); int work(); };
Worker::Worker() { sink(); }
int Worker::work() { sink(); return 1; }
class Direct { public: Direct() { sink(); } };
int invoke() { choose(1); Worker w; Direct d; return w.work(); }
