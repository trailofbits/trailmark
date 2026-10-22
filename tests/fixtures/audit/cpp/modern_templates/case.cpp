template<class T> concept Addable = requires(T x) { x + x; };
template<Addable T> T add(T a, T b) { return a + b; }
template<class T> class Box {
public:
    T get(T value) { return value; }
    template<class U> U convert(U value) { return value; }
};
int invoke() { return add<int>(1, 2); }
