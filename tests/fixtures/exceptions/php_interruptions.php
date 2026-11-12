<?php
namespace App;

class Payload {
    public $target;

    public function __wakeup() {
        throw new \RuntimeException("incomplete initialization");
    }

    public function __unserialize(array $data): void {
        $this->target = $data["target"];
        mutate($this);
        throw new \UnexpectedValueException("interrupted initialization");
    }

    public function __toString(): string {
        mutate($this);
        throw new \Error("conversion failed");
    }

    public function __destruct() {
        sensitive($this->target);
    }
}

function decode($data) {
    set_error_handler(function($code, $message) {
        mutate_state();
        throw new \ErrorException($message);
    });
    try {
        $object = unserialize($data);
        consume((string)$object);
    } catch (\Exception $error) {
        recover();
    } finally {
        restore_error_handler();
        if (cleanup_failed()) {
            throw new \LogicException("cleanup failed");
        }
    }
}
