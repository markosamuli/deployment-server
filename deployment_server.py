from __future__ import print_function

import deployment_pb2
from google.protobuf.timestamp_pb2 import Timestamp
import time
import os
import tempfile
import boto3
from botocore.exceptions import ClientError
import shutil
import sh
import copy
import yaml
from sh import rsync, unzip
import logging
import sys


logger = logging.getLogger(__name__)

_ONE_DAY_IN_SECONDS = 60 * 60 * 24


def run_lifecycle_hook_scripts(deploy_root, lifecycle_event, default_env, hooks):

    def is_exe(fpath):
        return os.path.isfile(fpath) and os.access(fpath, os.X_OK)

    success = []
    script_env = copy.copy(default_env)
    script_env['LIFECYCLE_EVENT'] = lifecycle_event
    for hook in hooks.get(lifecycle_event, []):

        script = os.path.join(deploy_root, hook['location'])
        if not is_exe(script):
            logger.warning('%s is not executable', script)
            success.append(False)
            continue

        logger.info('Run %s hook %s', lifecycle_event, script)
        run = sh.Command(script)
        try:
            for line in run(_iter=True, _env=script_env):
                print(line)
            success.append(True)
        except:
            logger.exception('Failed to run command %s', script)
            success.append(False)

    return success


def download_from_s3(bucket_name, bucket_key, filename):

    s3 = boto3.resource('s3')
    obj = s3.Object(bucket_name, bucket_key)
    if not obj.content_length > 0:
        return False

    logger.info('Downloading artefact s3://%s/%s with content length %d', bucket_name, bucket_key, obj.content_length)

    try:
        s3.meta.client.download_file(
            Bucket=bucket_name,
            Key=bucket_key,
            Filename=filename)
    except ClientError:
        logger.exception('Failed to download artefact from S3')
        return False
    except IOError:
        logger.exception('Failed to write artefact to the disk')
        return False

    return True


# https://gist.github.com/thatalextaylor/7408395
def pretty_time_delta(seconds):
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days > 0:
        return '%dd %dh %dm %ds' % (days, hours, minutes, seconds)
    elif hours > 0:
        return '%dh %dm %ds' % (hours, minutes, seconds)
    elif minutes > 0:
        return '%dm %ds' % (minutes, seconds)
    else:
        return '%ds' % (seconds,)


class DeployerServicer(deployment_pb2.BetaDeployerServicer):

    _events = []

    def new_event(self, request, new_status, message=None, lifecycle_event=None):

        timestamp = Timestamp()
        timestamp.GetCurrentTime()

        try:
            deployment_event = deployment_pb2.DeploymentEvent(
                project=request.project,
                environment=request.environment,
                status=new_status,
                message=message,
                timestamp=timestamp,
                lifecycle_event=lifecycle_event)
            self._events.append(deployment_event)
        except Exception as e:
            logger.exception(e)
            raise e

        return deployment_event

    def ListDeploymentEvents(self, request, context):
        for event in self._events:
            if request.project and event.project != request.project:
                continue
            if request.environment and event.environment != request.environment:
                continue
            yield event

    def Deploy(self, request, context):

        start_time = time.time()

        script_env = {
            'PROJECT_NAME': request.project,
            'DEPLOYMENT_ENVIRONMENT': request.environment,
        }

        # Return the initial PENDING event
        event = self.new_event(request, deployment_pb2.DeploymentEvent.CREATED,
                               message='New deployment created')
        yield event

        # Start deployment, return INCOMPLETE event
        event = self.new_event(request, deployment_pb2.DeploymentEvent.QUEUED,
                               message='Deployment started')
        yield event

        ###
        # ApplicationStop
        ###

        # run_lifecycle_hook_scripts(deploy_tmp, 'ApplicationStop', script_env, hooks)

        ###
        # DownloadBundle
        ###

        # Download artefact from S3
        event = self.new_event(request, deployment_pb2.DeploymentEvent.IN_PROGRESS,
                               message='Downloading deployment artefact file from S3',
                               lifecycle_event='DownloadBundle')
        yield event

        deploy_artefact_tmp = tempfile.mkstemp(prefix='deploy.', suffix='.zip')
        deploy_artefact = deploy_artefact_tmp[1]
        logger.info('Downloading eployment artefact from s3://%s/%s to %s',
                    request.artefact.s3_bucket,
                    request.artefact.s3_key,
                    deploy_artefact)
        download_success = download_from_s3(request.artefact.s3_bucket, request.artefact.s3_key, deploy_artefact)
        if not download_success:
            logger.info('Artefact download failed')
            event = self.new_event(request, deployment_pb2.DeploymentEvent.FAILED,
                                   message='Failed to download build artefact',
                                   lifecycle_event='DownloadBundle')
            yield event
            return

        event = self.new_event(request, deployment_pb2.DeploymentEvent.IN_PROGRESS,
                               message='Extract deployment artefact',
                               lifecycle_event='DownloadBundle')
        yield event

        deploy_tmp = tempfile.mkdtemp()
        logger.info('Extracting deployment artefact %s to %s', deploy_artefact, deploy_tmp)
        try:
            for line in unzip(deploy_artefact, '-d', deploy_tmp, _iter=True):
                print(line)
        except sh.ErrorReturnCode:
            logger.exception("Error when extracting %s archive", deploy_artefact)
            event = self.new_event(request, deployment_pb2.DeploymentEvent.FAILED,
                                   message='Failed to extract files from the build archive',
                                   lifecycle_event='DownloadBundle')
            yield event
            return

        appspec = None
        appspec_file = os.path.join(deploy_tmp, 'appspec.yml')
        logger.info('Reading appspec.yml file from %s', appspec_file)
        try:
            with open(appspec_file, 'r') as s:
                try:
                    appspec = yaml.load(s)
                except yaml.YAMLError:
                    logger.exception("Error when parsing appspec.yml file")
        except IOError:
            logger.exception("I/O error when opening appspec.yml file")

        if not appspec:
            logger.info('Could not load appspec.yml file')
            event = self.new_event(request, deployment_pb2.DeploymentEvent.FAILED,
                                   message='Could not load appspec.yml file',
                                   lifecycle_event='DownloadBundle')
            yield event
            return

        files = appspec.setdefault('files', [])
        hooks = appspec.setdefault('hooks', dict())

        ###
        # BeforeInstall
        ###

        if hooks.get('BeforeInstall', None):

            event = self.new_event(request, deployment_pb2.DeploymentEvent.IN_PROGRESS,
                                   message='Run BeforeInstall scripts',
                                   lifecycle_event='BeforeInstall')
            yield event

            scripts_success = run_lifecycle_hook_scripts(deploy_tmp, 'BeforeInstall', script_env, hooks)
            if all(scripts_success):
                logger.info('All BeforeInstall hooks completed')
                event = self.new_event(request, deployment_pb2.DeploymentEvent.IN_PROGRESS,
                                       message='BeforeInstall hooks completed',
                                       lifecycle_event='BeforeInstall')
                yield event
            else:
                logger.warning('Failed to run some BeforeInstall hooks')
                event = self.new_event(request, deployment_pb2.DeploymentEvent.FAILED,
                                       message='Failed to run BeforeInstall hooks',
                                       lifecycle_event='BeforeInstall')
                yield event
                return

        else:
            logger.info('No BeforeInstall hooks found, skipping...')

        event = self.new_event(request, deployment_pb2.DeploymentEvent.IN_PROGRESS,
                               message='Copy files',
                               lifecycle_event='Install')
        yield event

        if not files:
            event = self.new_event(request, deployment_pb2.DeploymentEvent.FAILED,
                                   message='No files to install',
                                   lifecycle_event='Install')
            yield event

        files_success = []
        for f in files:

            try:
                source = os.path.join(deploy_tmp, f['source'])
                destination = f['destination']
            except KeyError:
                logger.exception('Invalid appspec.yml files configuration')
                files_success.append(False)
                continue

            logger.info("Copying files from %s to %s", source, destination)
            try:
                if os.path.isdir(source):
                    for line in rsync("-avr", source + '/', destination, _iter=True):
                        print(line)
                else:
                    print(rsync("-av", source, destination))
                files_success.append(True)
            except sh.ErrorReturnCode:
                logger.exception('Copying files failed')
                files_success.append(False)

        if all(files_success):
            logger.info('All files copied')
        else:
            logger.warning('Failed to copy some files')
            event = self.new_event(request, deployment_pb2.DeploymentEvent.FAILED,
                                   message='Failed to copy files',
                                   lifecycle_event='Install')
            yield event
            return

        if hooks.get('AfterInstall', None):

            event = self.new_event(request, deployment_pb2.DeploymentEvent.IN_PROGRESS,
                                   message='Running AfterInstall scripts',
                                   lifecycle_event='AfterInstall')
            yield event

            scripts_success = run_lifecycle_hook_scripts(deploy_tmp, 'AfterInstall', script_env, hooks)
            if all(scripts_success):
                logger.info('All AfterInstall hooks completed')
                event = self.new_event(request, deployment_pb2.DeploymentEvent.IN_PROGRESS,
                                       message='AfterInstall hooks completed',
                                       lifecycle_event='AfterInstall')
                yield event
            else:
                logger.warning('Failed to run some AfterInstall hooks')
                event = self.new_event(request, deployment_pb2.DeploymentEvent.FAILED,
                                       message='Failed to run AfterInstall hooks',
                                       lifecycle_event='AfterInstall')
                yield event
                return

        else:
            logger.info('No AfterInstall hooks found, skipping...')

        logger.info('Cleaning up deployment files...')
        # event = self.new_event(request, deployment_pb2.DeploymentEvent.IN_PROGRESS,
        #                        message='Cleanup deployment files',
        #                        lifecycle_event='End')
        # yield event

        os.remove(deploy_artefact)
        shutil.rmtree(deploy_tmp)

        end_time = time.time()
        duration = pretty_time_delta(end_time - start_time)
        logger.info('Deployment completed in %s', duration)

        event = self.new_event(request, deployment_pb2.DeploymentEvent.SUCCEEDED,
                               message='Deployment completed in %s' % duration,
                               lifecycle_event='End')
        yield event


def serve():

    kw = {
        'format': '[%(asctime)s] %(message)s',
        'datefmt': '%m/%d/%Y %H:%M:%S',
        'level': logging.INFO,
        'stream': sys.stdout,
    }
    logging.basicConfig(**kw)

    server_port = os.environ.get('PORT', 50000)

    server = deployment_pb2.beta_create_Deployer_server(DeployerServicer())
    server.add_insecure_port('[::]:' + str(server_port))
    server.start()

    logger.info("Listening to insecure port %s", server_port)

    try:
        while True:
            time.sleep(_ONE_DAY_IN_SECONDS)
    except KeyboardInterrupt:
        server.stop(0)

if __name__ == '__main__':
    serve()
