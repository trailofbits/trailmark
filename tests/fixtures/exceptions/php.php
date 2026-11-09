<?php
function f() {
    try { throw new \App\Bad(); }
    catch (\App\Bad|Other $error) { observe(); throw $error; }
    finally { cleanup(); }
}
