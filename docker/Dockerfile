# Use Alpine as the base image
FROM alpine:latest

# Install required packages
RUN apk add --no-cache bash zsh git gcc python3-dev musl-dev linux-headers py3-pip ffmpeg htop screen figlet

# Clone the pikaraoke repository
RUN git clone -b PiKaraoke-Docker https://github.com/honestlai/pikaraoke.git /pikaraoke

# Run the setup script
RUN cd /pikaraoke && echo y | bash setup.sh

# Copy the entrypoint script into the container
COPY entrypoint.sh /entrypoint.sh

# Set permissions for the entrypoint script
RUN chmod +x /entrypoint.sh

# Set the entrypoint
ENTRYPOINT ["/entrypoint.sh"]
