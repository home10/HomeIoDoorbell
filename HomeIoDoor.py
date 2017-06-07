#!/usr/bin/env python2.7
import traceback

import cv2
import pika
import json
import time
import numpy
import requests
import os
from threading import Thread
from RPi import GPIO
from uuid import uuid1

door_name = 'DoorBell_1'
seen_persons = 0
reported = False

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
force_send_picture = False

door_open_time = 0

doorbell_rings = {}


def rabbitmq_callback(ch, method, properties, body):
    global door_open_time
    print body
    print 'At: %d' % time.time()
    try:
        inputmsg = json.loads(body)
    except ValueError:
        traceback.print_exc()
        return
    try:
        if inputmsg['origin'] != door_name:
            print "Not my door!"
            return

        if inputmsg['id'] not in doorbell_rings:
            if 'canceled' in doorbell_rings[inputmsg['id']] and \
                    doorbell_rings[inputmsg['id']]['canceled']:
                print 'Ring was already canceled'
                return

        # TODO open door
        print "Door %s by %s on %d" % \
              ("opened" if inputmsg['allowed'] else 'closed', inputmsg['user'], time.time())
        door_open_time = time.time()  # inputmsg['timestamp']

        msg = {'id': inputmsg['id'],
               'timestamp': time.time(),
               'type': 'doorbell-cancel',
               'origin': door_name,
               'allowed': inputmsg['allowed'],
               'user': inputmsg['user']}

        rabbitmq_channel.basic_publish(exchange='chat', routing_key='', body=json.dumps(msg))

        doorbell_rings[inputmsg['id']]['canceled'] = True
        doorbell_rings[inputmsg['id']]['response'] = inputmsg
        doorbell_rings[inputmsg['id']]['cancel_msg'] = msg
    except KeyError:
        traceback.print_exc()


rabbitmq_callback_channel.basic_consume(rabbitmq_callback,
                                        queue=callback_name,
                                        no_ack=True)


def gpio_button_click(channel):
    global force_send_picture
    time.sleep(0.1)
    if not GPIO.input(channel):
        print("Force Sending")
        force_send_picture = True


GPIO.setmode(GPIO.BCM)
GPIO.setup(17, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.add_event_detect(17, GPIO.FALLING, callback=gpio_button_click, bouncetime=300)

GPIO.setup(27, GPIO.OUT)
door_led_pwm = GPIO.PWM(27, 50)


def uploadImage(data_file):
    # type: (str) -> str
    resp = requests.post(url=imageupload_url,
                         files={'userfile': (data_file, open(data_file, 'rb'), 'image/jpeg')})
    return imagedownload_url % resp.content


def reportRing(img):
    # type: (numpy.ndarray) -> None
    print 'Reporting user at %d' % time.time()
    if img is not None:
        tmp_file = '/tmp/%d-img.jpg' % time.time()
        cv2.imwrite(tmp_file, img)
        img_url = uploadImage(tmp_file)
        os.remove(tmp_file)
    else:
        img_url = imagedownload_url_default

    msg = {'id': str(uuid1()),
           'timestamp': time.time(),
           'type': 'doorbell-ring',
           'origin': door_name,
           'picture_url': img_url}
    print json.dumps(msg)
    rabbitmq_channel.basic_publish(exchange='chat', routing_key='', body=json.dumps(msg))


def start_thread():
    global callback_thread
    callback_thread = Thread(target=rabbitmq_callback_channel.start_consuming)
    callback_thread.start()


def handle_image(frame):
    global reported, seen_persons, face_cascade, thread_running, force_send_picture
    try:
        grayscale = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        faces = face_cascade.detectMultiScale(
            grayscale,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(30, 30),
            flags=cv2.CASCADE_SCALE_IMAGE
        )

        #    for (x, y, w, h) in faces:
        #        cv2.rectangle(frame, (x, y), (x + w, y + h), ((0, 255, 0) if reported else (0, 0, 255)), 2)


        if force_send_picture:
            force_send_picture = False
            reportRing(frame)
            reported = True
        else:
            if len(faces) > 0:
                seen_persons = min(seen_persons + 1, 3)
                if not reported and seen_persons == 3:
                    reportRing(frame)
                    reported = True
            else:
                seen_persons = max(seen_persons - 1, 0)
                if seen_persons == 0:
                    reported = False
    except:
        pass
    thread_running = False


def main():
    global face_cascade, thread_running, door_led_pwm, door_open_time
    thread_running = False
    start_thread()

    door_led_pwm.start(0)

    vc = cv2.VideoCapture(0)

    face_cascade = cv2.CascadeClassifier('haarcascade_frontalface_default.xml')

    while True:
        ret, frame = vc.read()

        if not ret:
            print "FATAL got no frame!"
            break

        if not thread_running:
            print('Starting thread')
            thread_running = True
            process_thread = Thread(target=handle_image, args=(frame,))
            process_thread.start()

        framew, frameh, framed = frame.shape

        door_open_delta = time.time() - door_open_time
        if door_open_delta < 10:
            door_led_pwm.ChangeDutyCycle(min(100, max(0, door_open_delta * 10)))
            cv2.rectangle(frame, (0, 0), (frameh - 1, framew - 1), (0, 255, 0), 2)
        else:
            door_led_pwm.ChangeDutyCycle(0)

        cv2.imshow("HomeIoDoor", frame)

        pressed_key = cv2.waitKey(32) & 0xFF

        if pressed_key == ord('q'):
            break
        elif pressed_key == ord('s'):
            reportRing(frame)
        elif pressed_key == ord('t'):
            print("OPEN THE DAM DOOR")
            door_open_time = time.time()


if __name__ == '__main__':
    try:
        main()
    except:
        door_led_pwm.stop()
        GPIO.cleanup()
        raise
