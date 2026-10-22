<?php
trait ActionTrait { public function fromTrait(): void {} }
class Base { protected function parentAction(): void {} }
class Actions extends Base {
    use ActionTrait;
    public static function staticAction(): void {}
    public function instanceAction(): void {}
    public function invokeForms(): void {
        $this->instanceAction();
        $this?->instanceAction();
        self::staticAction();
        static::staticAction();
        Actions::staticAction();
        parent::parentAction();
        $this->fromTrait();
        $reference = $this->instanceAction(...);
    }
}
