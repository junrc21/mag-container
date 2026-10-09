globalThis.fetch = async (input, options = {}) => new Response(JSON.stringify({
  url: String(input),
  key: options.headers?.['x-internal-key'],
}), { status: 200, headers: { 'content-type': 'application/json' } });
