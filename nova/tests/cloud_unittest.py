# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import json
import logging
import os
import StringIO
import tempfile
import time

from tornado import ioloop
from twisted.internet import defer
import unittest
from xml.etree import ElementTree

from nova import db
from nova import flags
from nova import rpc
from nova import test
from nova import utils
from nova.auth import manager
from nova.compute import power_state
from nova.endpoint import api
from nova.endpoint import cloud
from nova.objectstore import image


FLAGS = flags.FLAGS


# Temp dirs for working with image attributes through the cloud controller
# (stole this from objectstore_unittest.py)
OSS_TEMPDIR = tempfile.mkdtemp(prefix='test_oss-')
IMAGES_PATH = os.path.join(OSS_TEMPDIR, 'images')
os.makedirs(IMAGES_PATH)

class CloudTestCase(test.BaseTestCase):
    def setUp(self):
        super(CloudTestCase, self).setUp()
        self.flags(connection_type='fake', images_path=IMAGES_PATH)

        self.conn = rpc.Connection.instance()
        logging.getLogger().setLevel(logging.DEBUG)

        # set up our cloud
        self.cloud = cloud.CloudController()

        # set up a service
        self.compute = utils.import_class(FLAGS.compute_manager)
        self.compute_consumer = rpc.AdapterConsumer(connection=self.conn,
                                                     topic=FLAGS.compute_topic,
                                                     proxy=self.compute)
        self.injected.append(self.compute_consumer.attach_to_tornado(self.ioloop))

        try:
            manager.AuthManager().create_user('admin', 'admin', 'admin')
        except: pass
        admin = manager.AuthManager().get_user('admin')
        project = manager.AuthManager().create_project('proj', 'admin', 'proj')
        self.context = api.APIRequestContext(handler=None,project=project,user=admin)

    def tearDown(self):
        manager.AuthManager().delete_project('proj')
        manager.AuthManager().delete_user('admin')

    def test_console_output(self):
        if FLAGS.connection_type == 'fake':
            logging.debug("Can't test instances without a real virtual env.")
            return
        instance_id = 'foo'
        inst = yield self.compute.run_instance(instance_id)
        output = yield self.cloud.get_console_output(self.context, [instance_id])
        logging.debug(output)
        self.assert_(output)
        rv = yield self.compute.terminate_instance(instance_id)

    def test_run_instances(self):
        if FLAGS.connection_type == 'fake':
            logging.debug("Can't test instances without a real virtual env.")
            return
        image_id = FLAGS.default_image
        instance_type = FLAGS.default_instance_type
        max_count = 1
        kwargs = {'image_id': image_id,
                  'instance_type': instance_type,
                  'max_count': max_count}
        rv = yield self.cloud.run_instances(self.context, **kwargs)
        # TODO: check for proper response
        instance = rv['reservationSet'][0][rv['reservationSet'][0].keys()[0]][0]
        logging.debug("Need to watch instance %s until it's running..." % instance['instance_id'])
        while True:
            rv = yield defer.succeed(time.sleep(1))
            info = self.cloud._get_instance(instance['instance_id'])
            logging.debug(info['state'])
            if info['state'] == power_state.RUNNING:
                break
        self.assert_(rv)

        if connection_type != 'fake':
            time.sleep(45) # Should use boto for polling here
        for reservations in rv['reservationSet']:
            # for res_id in reservations.keys():
            #  logging.debug(reservations[res_id])
             # for instance in reservations[res_id]:
           for instance in reservations[reservations.keys()[0]]:
               logging.debug("Terminating instance %s" % instance['instance_id'])
               rv = yield self.compute.terminate_instance(instance['instance_id'])

    def test_instance_update_state(self):
        def instance(num):
            return {
                'reservation_id': 'r-1',
                'instance_id': 'i-%s' % num,
                'image_id': 'ami-%s' % num,
                'private_dns_name': '10.0.0.%s' % num,
                'dns_name': '10.0.0%s' % num,
                'ami_launch_index': str(num),
                'instance_type': 'fake',
                'availability_zone': 'fake',
                'key_name': None,
                'kernel_id': 'fake',
                'ramdisk_id': 'fake',
                'groups': ['default'],
                'product_codes': None,
                'state': 0x01,
                'user_data': ''
            }
        rv = self.cloud._format_describe_instances(self.context)
        self.assert_(len(rv['reservationSet']) == 0)

        # simulate launch of 5 instances
        # self.cloud.instances['pending'] = {}
        #for i in xrange(5):
        #    inst = instance(i)
        #    self.cloud.instances['pending'][inst['instance_id']] = inst

        #rv = self.cloud._format_instances(self.admin)
        #self.assert_(len(rv['reservationSet']) == 1)
        #self.assert_(len(rv['reservationSet'][0]['instances_set']) == 5)
        # report 4 nodes each having 1 of the instances
        #for i in xrange(4):
        #    self.cloud.update_state('instances', {('node-%s' % i): {('i-%s' % i): instance(i)}})

        # one instance should be pending still
        #self.assert_(len(self.cloud.instances['pending'].keys()) == 1)

        # check that the reservations collapse
        #rv = self.cloud._format_instances(self.admin)
        #self.assert_(len(rv['reservationSet']) == 1)
        #self.assert_(len(rv['reservationSet'][0]['instances_set']) == 5)

        # check that we can get metadata for each instance
        #for i in xrange(4):
        #    data = self.cloud.get_metadata(instance(i)['private_dns_name'])
        #    self.assert_(data['meta-data']['ami-id'] == 'ami-%s' % i)

    def test_user_editable_image_endpoint(self):
        pathdir = os.path.join(FLAGS.images_path, 'ami-testing')
        os.mkdir(pathdir)
        info = {}
        with open(os.path.join(pathdir, 'info.json'), 'w') as f:
            json.dump(info, f)
        yield self.cloud.set_image_description(self.context, 'ami-testing',
                                               'Foo Img')
        img = image.Image('ami-testing')
        self.assertEqual('Foo Img', img.metadata['displayDescription'])
        self.cloud.set_image_description(self.context, 'ami-testing', '')
        self.assert_(not 'displayDescription' in img.metadata)

    def test_update_of_instance_display_fields(self):
        inst = db.instance_create({}, {})
        self.cloud.update_instance(self.context, inst['id'],
                                   display_name='c00l 1m4g3')
        inst = db.instance_get({}, inst['id'])
        self.assertEqual('c00l 1m4g3', inst['display_name'])
        db.instance_destroy({}, inst['id'])

    def test_update_of_instance_wont_update_private_fields(self):
        inst = db.instance_create({}, {})
        self.cloud.update_instance(self.context, inst['id'],
                                   mac_address='DE:AD:BE:EF')
        inst = db.instance_get({}, inst['id'])
        self.assertEqual(None, inst['mac_address'])
        db.instance_destroy({}, inst['id'])

    def test_update_of_volume_display_fields(self):
        vol = db.volume_create({}, {})
        self.cloud.update_volume(self.context, vol['id'],
                                 display_name='c00l v0lum3')
        vol = db.volume_get({}, vol['id'])
        self.assertEqual('c00l v0lum3', vol['display_name'])
        db.volume_destroy({}, vol['id'])

    def test_update_of_volume_wont_update_private_fields(self):
        vol = db.volume_create({}, {})
        self.cloud.update_volume(self.context, vol['id'],
                                   mountpoint='/not/here')
        vol = db.volume_get({}, vol['id'])
        self.assertEqual(None, vol['mountpoint'])
        db.volume_destroy({}, vol['id'])
