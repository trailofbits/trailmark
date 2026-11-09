<?php
// Local characterization only: no files, network, or attacker-controlled input.
$events = [];
$state = "checked";

class ConversionProbe {
    public function __toString(): string {
        $GLOBALS["state"] = "changed";
        $GLOBALS["events"][] = "convert";
        throw new Error("conversion");
    }
}

function convert() {
    try {
        $text = (string)new ConversionProbe();
    } catch (Exception $error) {
        $GLOBALS["events"][] = "exception-handler";
    } finally {
        $GLOBALS["events"][] = "finally";
    }
}

try {
    convert();
} catch (Error $error) {
    $events[] = "error-handler";
}

set_error_handler(function($code, $message) {
    $GLOBALS["events"][] = "warning-callback";
    throw new ErrorException($message);
}, E_USER_WARNING);
try {
    trigger_error("local probe", E_USER_WARNING);
} catch (ErrorException $error) {
    $events[] = "converted-warning";
} finally {
    restore_error_handler();
}

class RevivalProbe {
    public function __wakeup() {
        $GLOBALS["events"][] = "wakeup";
    }

    public function __unserialize(array $data): void {
        $GLOBALS["events"][] = "unserialize";
        throw new UnexpectedValueException("partial initialization");
    }
}

try {
    unserialize(serialize(new RevivalProbe()));
} catch (UnexpectedValueException $error) {
    $events[] = "initialization-failed";
}

try {
    try {
        throw new RuntimeException("original");
    } finally {
        throw new LogicException("cleanup");
    }
} catch (Throwable $error) {
    $events[] = get_class($error) . ":" . get_class($error->getPrevious());
}

echo json_encode(["version" => PHP_VERSION, "state" => $state, "events" => $events]);
