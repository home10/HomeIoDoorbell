#!/usr/bin/env python2.7
from StringIO import StringIO

import cv2
import pika
import json
import time
import numpy
import requests
import os
from threading import Thread

door_name = 'DoorBell_1'

imageupload_url = 'https://rickrongen.nl/ims/'
imagedownload_url = 'https://rickrongen.nl/ims/?id=%s'
imagedownload_url_default = 'http://sharing.abc15.com/sharewcpo/photo/2016/09/23' \
                            '/16061700839_a8999325f9_z_1474637412646_46803962_ver1.0_640_480.jpg '

rabbitmq_credentials = pika.PlainCredentials('dmtuwgyf', '-uO2kShACltC63RsmWE3eQMwQ0vbr-Pq')
rabbitmq_params = pika.ConnectionParameters(host='orangutan.rmq.cloudamqp.com', port=5672, virtual_host='dmtuwgyf',
                                            credentials=rabbitmq_credentials)
rabbitmq_connection = pika.BlockingConnection(rabbitmq_params)
rabbitmq_channel = rabbitmq_connection.channel()

rabbitmq_callback_channel = rabbitmq_connection.channel()
rabbitmq_callback_channel.exchange_declare(exchange='callback', type='direct', durable=True)
callback_result = rabbitmq_callback_channel.queue_declare(exclusive=True)
callback_name = callback_result.method.queue

rabbitmq_callback_channel.queue_bind(exchange='callback', queue=callback_name, routing_key=door_name)
callback_thread = None

door_open_time = time.time()


def callback(ch, method, properties, body):
    global door_open_time
    print body
    inputmsg = json.loads(body)
    if inputmsg['origin'] != door_name:
        print "Not my door!"
        return

    # TODO open door
    print "Door %s by %s" % ("opened" if inputmsg['allowed'] else 'closed', inputmsg['user'])
    door_open_time = inputmsg['timestamp']

    msg = {'timestamp': time.time(),
           'type': 'doorbell-cancel',
           'origin': door_name,
           'allowed': inputmsg['allowed'],
           'user': inputmsg['user']}

    rabbitmq_channel.basic_publish(exchange='chat', routing_key='', body=json.dumps(msg))


rabbitmq_callback_channel.basic_consume(callback,
                                        queue=callback_name,
                                        no_ack=True)


def uploadImage(data_file):
    # type: (str) -> str
    resp = requests.post(url=imageupload_url, files={'userfile': (data_file, open(data_file, 'rb'), 'image/jpeg')})
    return imagedownload_url % resp.content


def reportRing(img):
    # type: (numpy.ndarray) -> None
    if img is not None:
        tmp_file = '/tmp/%d-img.jpg' % time.time()
        cv2.imwrite(tmp_file, img)
        img_url = uploadImage(tmp_file)
        os.remove(tmp_file)
    else:
        img_url = imagedownload_url_default

    msg = {'timestamp': time.time(),
           'type': 'doorbell-ring',
           'origin': door_name,
           'picture_url': img_url}
    rabbitmq_channel.basic_publish(exchange='chat', routing_key='', body=json.dumps(msg))


def start_thread():
    global callback_thread
    callback_thread = Thread(target=rabbitmq_callback_channel.start_consuming)
    callback_thread.start()


def main():
    start_thread()
    vc = cv2.VideoCapture(0)

    face_cascade = cv2.CascadeClassifier('haarcascade_frontalface_default.xml')
    seen_persons = 0
    reported = False

    while True:
        ret, frame = vc.read()

        if not ret:
            print "FATAL got no frame!"
            break

        framew, frameh, framed = frame.shape

        grayscale = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        faces = face_cascade.detectMultiScale(
            grayscale,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(30, 30),
            flags=cv2.CASCADE_SCALE_IMAGE
        )

        for (x, y, w, h) in faces:
            cv2.rectangle(frame, (x, y), (x + w, y + h), ((0, 255, 0) if reported else (0, 0, 255)), 2)

        if door_open_time + 10 >= time.time():
            cv2.rectangle(frame, (0, 0), (frameh - 1, framew - 1), (0, 255, 0), 2)

        if len(faces) > 0:
            seen_persons = min(seen_persons + 1, 10)
            if not reported and seen_persons == 10:
                reportRing(frame)
                reported = True
        else:
            seen_persons = max(seen_persons - 1, 0)
            if seen_persons == 0:
                reported = False

        cv2.imshow("HomeIoDoor", frame)

        pressed_key = cv2.waitKey(240) & 0xFF

        if pressed_key == ord('q'):
            break
        elif pressed_key == ord('s'):
            reportRing(frame)


if __name__ == '__main__':
    main()
