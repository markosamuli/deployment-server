# deployment-server

This is a sandbox project. Use it at your own discretion.

I've built it as a server for the [deployment-slackbot](https://github.com/markosamuli/deployment-slackbot) client for
playing with Google's [GRPC](http://www.grpc.io/) framework.

Protocol Buffers [proto3](https://developers.google.com/protocol-buffers/docs/proto3) version of the language is used. It's available as a beta release at the time of writing this.

## Deployer service

### Deploy

This RPC mimics the deployment lifecycle of [AWS CodeDeploy](https://aws.amazon.com/documentation/codedeploy/) service and
uses similar `appspec.yml` configuration files in the deployment artefacts. It does not implement full AppSpec file to
the specification or all lifecycle steps.

It opens a streaming connection with the client and I would expect it to stop working if this
connection is interrupted.

### ListDeploymentEvents

This RPC just lists the previous deployment events from the in-memory storage.
