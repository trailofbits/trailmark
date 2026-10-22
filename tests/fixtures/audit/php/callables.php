<?php
function dormantSink(): void { echo 'dormant'; }
function arrowSink(): int { echo 'arrow'; return 1; }
function callableSink(): void { echo 'callable'; }
function buildCallbacks(): array {
    $closure = function (int $value): void {
        if ($value > 0) {
            dormantSink();
            throw new RuntimeException('later');
        }
    };
    $arrow = fn(): int => arrowSink();
    $reference = callableSink(...);
    return [$closure, $arrow, $reference];
}
