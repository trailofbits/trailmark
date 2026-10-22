interface Service { run(): void; }
class Safe implements Service { run(): void {} }
class Risky implements Service { run(): void {} }

export function reassigned(replacement: Service): void {
    let receiver: Service = new Safe();
    receiver = replacement;
    receiver.run();
}

export function blockShadow(): void {
    const receiver = new Safe();
    { const receiver = new Risky(); receiver.run(); }
    receiver.run();
}

export function branch(flag: boolean): void {
    let receiver: Service = new Safe();
    if (flag) { receiver = new Risky(); }
    receiver.run();
}
