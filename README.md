# deployment-server

## Archived

This repository is no longer maintained, so please use any of the code at your
own discretion.

The outdated [PyYAML] dependency in the project contains a potential security
vulnerability [CVE-2017-18342] so please patch if you decide to use this code
for something.

[PyYAML]: https://pypi.org/project/pyaml/
[CVE-2017-18342]: https://nvd.nist.gov/vuln/detail/CVE-2017-18342

## Description

This is a sandbox project I created for learning purposes.

I've built it as a server for the [deployment-slackbot] client for
playing with Google's [gRPC] framework.

Protocol Buffers [proto3] version of the language is used. It's available as a
beta release at the time of writing this.

[deployment-slackbot]: https://github.com/markosamuli/deployment-slackbot
[proto3]: https://developers.google.com/protocol-buffers/docs/proto3
[gRPC]: http://www.grpc.io/

## Configuration

Make sure you have valid AWS IAM credentials set in environment variables or
are using IAM instance role.

These are required for [boto3] to download artefacts from AWS S3.

[boto3]: https://github.com/boto/boto3

## Running the server

```bash
python deployment_server.py
```

## Deployer service

### Deploy

This RPC mimics the deployment lifecycle of [AWS CodeDeploy] service and
uses similar `appspec.yml` configuration files in the deployment artefacts.

It does not implement full AppSpec file to the specification or all lifecycle
steps.

It opens a streaming connection with the client and I would expect it to stop 
working if this connection is interrupted.

[AWS CodeDeploy]: https://aws.amazon.com/documentation/codedeploy/

### ListDeploymentEvents

This RPC just lists the previous deployment events from the in-memory storage.

## License

* [MIT License](LICENSE)

## Author

* [@markosamuli](https://github.com/markosamuli)
