#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import logging
# We need to use multiprocessing.dummy, since we use the Pool to run
# update_project. This in turn uses anitya.lib.backends.BaseBackend.call_url,
# which utilizes a global requests session. Requests session is not usable
# with multiprocessing, since it would need to share the SSL connection between
# processes (see https://stackoverflow.com/q/3724900#3724938).
# multiprocessing.dummy.Pool is in fact a Thread pool, which works ok
# with a global shared requests session.
import multiprocessing.dummy as multiprocessing

from anitya.config import config
from anitya import db
from anitya.lib import utilities
import anitya
import anitya.lib.exceptions

LOG = logging.getLogger('anitya')


def indexed_listings():
    """ Return the full list of project names found by feed listing. """
    for backend in anitya.lib.plugins.get_plugins():
        LOG.info("Checking feed for backend %r" % backend)
        try:
            for name, homepage, backend, version in backend.check_feed():
                yield name, homepage, backend, version
        except NotImplementedError:
            LOG.debug("Skipping feed check for backend %r" % backend)
            # Not all backends have the check_feed classmethod implemented,
            # and that's okay...  Just ignore them for now.
            continue


def projects_by_feed(session):
    """ Return the list of projects out of sync, found by feed listings.

    If a new entry is noticed and we don't have a project for it, add it.
    """
    for name, homepage, backend, version in indexed_listings():
        project = db.Project.get_or_create(
            session, name, homepage, backend)
        if project.latest_version == version:
            LOG.debug("Project %s is already up to date." % project.name)
        else:
            yield project


def update_project(project_id):
    """ Check for updates on the specified project. """
    session = utilities.init(config['DB_URL'])
    project = db.Project.by_id(session, project_id)
    try:
        utilities.check_project_release(project, session),
    except anitya.lib.exceptions.AnityaException as err:
        LOG.info(err)
    finally:
        session.get_bind().dispose()
        session.remove()


def main(debug, feed):
    ''' Retrieve all the packages and for each of them update the release
    version.
    '''
    db.initialize(config)
    session = db.Session()
    run = db.Run(status='started')
    session.add(run)
    session.commit()
    LOG.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    if debug:
        # Console handler
        chand = logging.StreamHandler()
        chand.setLevel(logging.INFO)
        chand.setFormatter(formatter)
        LOG.addHandler(chand)

    # Save the logs in a file
    fhand = logging.FileHandler('/var/tmp/anitya_cron.log')
    fhand.setLevel(logging.INFO)
    fhand.setFormatter(formatter)
    LOG.addHandler(fhand)

    if feed:
        projects = list(projects_by_feed(session))
        session.commit()
    else:
        projects = db.Project.all(session)

    project_ids = [project.id for project in projects]

    N = config.get('CRON_POOL', 10)
    LOG.info("Launching pool (%i) to update %i projects", N, len(project_ids))
    p = multiprocessing.Pool(N)
    p.map(update_project, project_ids)

    run = db.Run(status='ended')
    session.add(run)
    session.commit()


if __name__ == '__main__':
    debug = '--debug' in sys.argv
    feed = '--check-feed' in sys.argv
    main(debug=debug, feed=feed)
