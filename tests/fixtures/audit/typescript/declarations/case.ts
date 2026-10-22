export interface Repository<T extends object> {
    load<U extends T>(key: string, fallback?: U): Promise<U>;
    save(value: T): void;
}
export function overloaded(value: string): string;
export function overloaded(value: number): number;
export function overloaded(value: string | number): string | number { return value; }
export namespace Security {
    export function validate(value: string): boolean { return value.length > 0; }
    export class Guard { check(value: string): boolean { return validate(value); } }
}
export function outside(): boolean { return Security.validate("input"); }
