#include <Python.h>

typedef struct TSLanguage TSLanguage;
const TSLanguage *TRAILMARK_LANGUAGE(void);

static PyObject *language(PyObject *Py_UNUSED(self), PyObject *Py_UNUSED(args)) {
    return PyCapsule_New((void *)TRAILMARK_LANGUAGE(), "tree_sitter.Language", NULL);
}

static PyMethodDef methods[] = {
    {"language", language, METH_NOARGS, "Return the bundled language capsule."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef module = {
    .m_base = PyModuleDef_HEAD_INIT,
    .m_name = "_binding",
    .m_size = -1,
    .m_methods = methods
};

PyMODINIT_FUNC PyInit__binding(void) {
    return PyModule_Create(&module);
}
