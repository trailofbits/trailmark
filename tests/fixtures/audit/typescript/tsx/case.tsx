declare global { namespace JSX { interface Element {} interface IntrinsicElements { button: any; } } }
function sink(): string { return "clicked"; }
export function View(): JSX.Element {
    return <button>{sink()}</button>;
}
export const Inline = () => <button>{sink()}</button>;
