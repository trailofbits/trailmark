void global_sink() {}
namespace alpha {
void sink() { global_sink(); }
void entry() { sink(); }
class Worker { public: void run() { sink(); } };
}
namespace beta {
void sink() {}
void entry() { sink(); }
class Worker { public: void run() { sink(); } };
}
namespace {
void private_sink() { global_sink(); }
void private_entry() { private_sink(); }
}
void qualified() { alpha::entry(); beta::entry(); private_entry(); }
