# -*- coding: utf-8 -*-
#  2020-09-24 Cornelius Kölbel <cornelius.koelbel@netknights.it>
#             Use Argon2
#  2017-08-11 Cornelius Kölbel <cornelius.koelbel@netknights.it>
#             initial writeup
#
#  License:  AGPLv3
#  contact:  http://www.privacyidea.org
#
# This code is free software; you can redistribute it and/or
# modify it under the terms of the GNU AFFERO GENERAL PUBLIC LICENSE
# License as published by the Free Software Foundation; either
# version 3 of the License, or any later version.
#
# This code is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNE7SS FOR A PARTICULAR PURPOSE.  See the
# GNU AFFERO GENERAL PUBLIC LICENSE for more details.
#
# You should have received a copy of the GNU Affero General Public
# License along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
from ..models import AuthCache, db
from sqlalchemy import and_
from passlib.hash import argon2
import datetime
import logging

ROUNDS = 9
log = logging.getLogger(__name__)


def _hash_password(password):
    return argon2.using(rounds=ROUNDS).hash(password)


def add_to_cache(username, realm, resolver, password):
    # Can not store timezone aware timestamps!
    first_auth = datetime.datetime.utcnow()
    auth_hash = _hash_password(password)
    record = AuthCache(username, realm, resolver, auth_hash, first_auth, first_auth)
    log.debug('Adding record to auth cache: ({!r}, {!r}, {!r}, {!r})'.format(
        username, realm, resolver, auth_hash))
    r = record.save()
    return r


def increment_auth_count(cache_id):
    db.session.query(AuthCache).filter(AuthCache.id == cache_id).update(
        {AuthCache.current_number_of_authentications: AuthCache.current_number_of_authentications + 1})
    db.session.commit()


def update_cache_last_auth(cache_id):
    last_auth = datetime.datetime.utcnow()
    AuthCache.query.filter(
        AuthCache.id == cache_id).update({"last_auth": last_auth})
    db.session.commit()


def delete_from_cache(username, realm, resolver, password):
    cached_auths = db.session.query(AuthCache).filter(AuthCache.username == username,
                                                      AuthCache.realm == realm,
                                                      AuthCache.resolver == resolver).all()
    r = 0
    for cached_auth in cached_auths:
        delete_entry = False
        # if the password does match, we deleted it.
        try:
            if argon2.verify(password, cached_auth.authentication):
                delete_entry = True
        except ValueError:
            log.debug("Old authcache entry for user {0!s}@{1!s}.".format(username, realm))
            # Also delete old entries
            delete_entry = True
        if delete_entry:
            r += 1
            cached_auth.delete()
    db.session.commit()
    return r


def cleanup(minutes):
    """
    Will delete all authcache entries, where last_auth column is older than
    the given minutes.

    :param minutes: Age of the last_authentication in minutes
    :type minutes: int
    :return:
    """
    cleanuptime = datetime.datetime.utcnow() - datetime.timedelta(minutes=minutes)
    r = db.session.query(AuthCache).filter(AuthCache.last_auth < cleanuptime).delete()
    db.session.commit()
    return r


def verify_in_cache(username, realm, resolver, password, first_auth=None, last_auth=None,
                    max_number_of_authentications=None):
    """
    Verify if the given credentials are cached and if the time is correct.
    
    :param username: 
    :param realm: 
    :param resolver: 
    :param password: 
    :param first_auth: The timestamp when the entry was first written to the 
        cache. Only find newer entries 
    :param last_auth: The timestamp when the entry was last successfully 
        verified. Only find newer entries
    :param max_number_of_authentications: Maximum number of times the authcache entry can be used to skip authentication,
        as defined by ACTION.AUTH_CACHE policy. Will return False if the current number of authentications + 1 of the
        cached authentication exceeds this value.
    :return: 
    """
    conditions = []
    result = False
    conditions.append(AuthCache.username == username)
    conditions.append(AuthCache.realm == realm)
    conditions.append(AuthCache.resolver == resolver)

    if first_auth:
        conditions.append(AuthCache.first_auth > first_auth)
    if last_auth:
        conditions.append(AuthCache.last_auth > last_auth)

    filter_condition = and_(*conditions)
    cached_auths = AuthCache.query.filter(filter_condition).all()

    for cached_auth in cached_auths:
        try:
            result = argon2.verify(password, cached_auth.authentication)
        except ValueError:
            log.debug("Old authcache entry for user {0!s}@{1!s}.".format(username, realm))
            result = False

        if result and max_number_of_authentications:
            result = cached_auth.current_number_of_authentications < max_number_of_authentications
            increment_auth_count(cached_auth.id)
            break

        if result:
            # Update the last_auth
            update_cache_last_auth(cached_auth.id)
            break

    if not result:
        # Delete older entries
        delete_from_cache(username, realm, resolver, password)

    return result
