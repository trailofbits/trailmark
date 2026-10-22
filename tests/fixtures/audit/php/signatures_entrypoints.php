<?php
use Symfony\Component\Routing\Attribute\Route;
interface Left {}
interface Right {}
class Input implements Left, Right {}
class Controller {
    public function __construct(public readonly Input $input) {}
    #[Route('/short')]
    public function shortRoute(Left&Right $input, string ...$rest): (Left&Right)|null { return $input; }
    #[\Symfony\Component\Routing\Attribute\Route('/qualified')]
    public function qualifiedRoute(?Input $input = null): ?Input { return $input; }
    /**
     * @Route("/legacy")
     */
    public function legacyRoute(): void {}
}
