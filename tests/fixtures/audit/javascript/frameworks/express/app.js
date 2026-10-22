import express from 'express';
const app = express();
function sink(request) { return request; }
app.get('/', (request, response) => { return sink(request); });
