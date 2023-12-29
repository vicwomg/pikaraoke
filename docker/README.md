
# Dockerized PiKaraoke

PiKaraoke is a "KTV"-style karaoke song search and queueing system, originally designed to work on Raspberry Pi, OSX, Windows, and Linux. This repository contains a Dockerized version of PiKaraoke, built on an alpine base, making it easy to set up and run on any system with Docker support.

## Features

- The Dockerized version retains all features of the original PiKaraoke.
- Easy to set up and run on any system with Docker support.
- Automatically exposes on port 5555.
- URL is required for reverse proxy setup with either http:// or https://.
- The PASSWORD variable is optional.

## Prerequisites

- Docker and Docker Compose installed on your system.
- A reverse proxy like nginx is very helpful but not required.

## Installation and Launch

### Using Docker Compose Command Line

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/honestlai/pikaraoke-docker.git
   cd pikaraoke-docker
   ```

2. **Create a Docker Compose File**:
   Create a `docker-compose.yml` file with the following content:
   ```yaml
   version: '3'

   services:
     pikaraoke:
       image: honestlai/pikaraoke-docker:latest
       container_name: PiKaraoke
       volumes:
         - pikaraoke-songs:/pikaraoke-songs
       environment:
         URL: #https://karaoke.yourdomain.com
         PASSWORD: #optionalpassword
       restart: unless-stopped
       ports:
         - "5555:5555"

   volumes:
     pikaraoke-songs:
       # Define your volume specifics here, if any.
   ```

3. **Running the Container**:
   Use Docker Compose to pull the image and start the container:
   ```bash
   docker-compose up -d
   ```

### Using Portainer

1. **Access Portainer**: Navigate to the 'Stacks' section.
2. **Add a New Stack**: Click on '+ Add stack'.
3. **Compose File**: Clone the repository or copy the `docker-compose.yml` content.
4. **Environment Variables**: Add necessary variables like `URL` and `PASSWORD`.
5. **Deploy the Stack**: Click on 'Deploy the stack'.

### Building the Container Locally

1. **Clone this Dockerized repository**:
   ```bash
   git clone https://github.com/honestlai/pikaraoke-docker.git
   cd pikaraoke-docker
   ```

2. **Build and Run the Docker Container**:
   ```bash
   docker-compose up --build
   ```

---

For more details on the project and additional features, please visit the [main repository page](https://github.com/vicwomg/pikaraoke).
