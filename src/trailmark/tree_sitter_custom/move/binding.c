#include <Python.h>
#include "tree_sitter/parser.h"

TSLanguage *tree_sitter_move(void);

static PyObject *language(PyObject *self, PyObject *args) {
    return PyCapsule_New(tree_sitter_move(), "tree_sitter.Language", NULL);
}

static PyMethodDef methods[] = {
    {"language", language, METH_NOARGS, "Get tree-sitter move language"},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "_binding",
    NULL,
    -1,
    methods
};

PyMODINIT_FUNC PyInit__binding(void) {
    return PyModule_Create(&module);
}
