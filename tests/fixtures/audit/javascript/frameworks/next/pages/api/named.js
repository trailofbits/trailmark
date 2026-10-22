function sink(request) { return request; }
export default function handler(request, response) { return sink(request); }
