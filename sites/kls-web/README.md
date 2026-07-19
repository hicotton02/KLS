# Keeping Law Simple

The public KLS experience, built with Next.js-compatible vinext and hosted on
OpenAI Sites. It reads the public KLS API and falls back to the complete state
directory when the API is temporarily unavailable.

## Development

```bash
npm install
npm run dev
npm test
```

Set `KLS_API_BASE_URL` to use a non-production API. The default is
`https://www.keepinglawsimple.org`.
