# Link Processor

Link Processor is a FastAPI service that generates redirect links with delayed callbacks. It allows you to create links that will redirect users to a specified URL while triggering an HTTP callback to your server after a configurable delay.

## Features

- Create short links with configurable redirect targets
- Schedule delayed callbacks to your server after link activation
- Secure parameter encoding using JWT
- Configurable delay periods (up to 1 hour)
- Docker-ready for easy deployment
