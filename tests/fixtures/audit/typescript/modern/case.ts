function sink(): string { return "ok"; }
function Get(_path: string): MethodDecorator { return () => {}; }
class Controller {
    @Get("/")
    run(): string { return sink(); }
    handler = (): string => { return sink(); };
}
export function identity<T extends object>(value: T): T { return value; }
export const concise = (): string => sink();
export function shadow(sink: () => string): string { return sink(); }
export function enclosing(): void {
    const nested = () => { if (true) sink(); };
}
declare const exports: { handler?: () => string };
exports.handler = function(): string { return sink(); };
