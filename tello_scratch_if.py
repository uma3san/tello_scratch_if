# -*- coding: utf-8 -*-

#
# 自己責任で、ご自由にお使いください。
#   2020年4月吉日
#   馬島良行
#


### to scratch
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

### to tello
import threading
import socket
import sys
import queue
import cv2
from time import sleep
from collections import deque

scratchAdrs          = ( 'localhost',    8001 )     #             PC <-> Scratch on PC
telloCmdAdrs         = ( '192.168.10.1', 8889 )     #  Tello <->  PC ( 50602 )
pcAdrs2SendCmd2Tello = ( '0.0.0.0',      50602 )    #  50602: 決めておけば何でも可。上は動かない。
                                                    #  tello は PCを(192.168.10.2)としている
rcvTelloStateAdrs    = ( '0.0.0.0',      8890 )     #  Tello  ->  PC
rcvTelloVideoURL     = ( 'udp://@0.0.0.0:11111' )   #  Tello  ->  PC

INTERVAL = 0.2

class SendCmd(threading.Thread):
    def __init__(self):
        self.sendSock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) # socket for receiving tello state
        self.sendSock.bind( pcAdrs2SendCmd2Tello )                # program を再起動する時、tello の再起動を避けるために設定。
        self.sendSock.settimeout(10.0)
        self.finishSignal = False
        self.result = False

        if self.connect() == True:
            super().__init__(daemon=True)
            self.start()
            self.result = True
    
    def connect(self):
        print("-> try to connect the tello", end="")
        counter = 5
        while counter > 0:
            self.sendSock.sendto('command'.encode('utf-8'), telloCmdAdrs) # tello を command モードにする
            try:
                response = self.sendSock.recvfrom(1024)                  # ctl-c で終了すると、telloを再起動しないと動かない -> client.bind で再起動は不要に
                                                                         # recvfrom は、データを受け取ると戻ってくる。(buffer が一杯でなくても)
                print(response) 
                if b'ok' in response:
                    print("-> connected : set tello to command mode")
                    return True

            except socket.timeout:
                print('\n???? socket timeout : ', counter-1, end="" )
                counter -= 1

            except KeyboardInterrupt:
                print('~~CTRL-C')
                return False
        else:
            return False

    #
    # Thread of sending command from PC to Tello
    #
    def run(self):
        while True:
            if self.finishSignal == True:
                self.sendSock.close()
                print(".... SendCmd : socket closed")
                return
                
            if len(cmdQue) != 0:
                if 'emergency' in cmdQue:
                    # 飛行停止コマンド
                    cmd = 'emergency'
                    cmdQue.clear()
                else:
                    # 通常のコマンド
                    cmd = cmdQue.popleft()

                print('{} {} {}'.format("->", cmd, " ... "), end="")
                self.sendSock.sendto(cmd.encode('utf-8'), telloCmdAdrs)

                try:
                    response = self.sendSock.recvfrom(1024)  # (b'ok', ('192.168.10.1', 8889)) 
                                                             #  byte    str             int     の tuple
                    if b'ok' in response:
                       print( response[0].decode('utf-8'), response[1][0], response[1][1])
                       pass 

                    elif b'error' in response:
                       print( response[0].decode('utf-8'), response[1][0], response[1][1])
                       # sys.exit()

                except socket.timeout:
                    print("???? send command error : recvfrom time out")
                    continue
            else:
                pass

    def kill_thread(self):
        self.finishSignal = True

class ReceiveTelloState(threading.Thread):
    def __init__(self):
        self.rcvSock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # socket for receiving tello state
        self.rcvSock.bind(rcvTelloStateAdrs)
        self.rcvSock.settimeout(1.0)
        self.finishSignal = False

        super().__init__(daemon=True)
        self.start()

    #
    #  The thread of receiving tello state
    #
    def run(self):
        while True:
            if self.finishSignal == True:
                self.rcvSock.close()
                print(".... ReceiveTelloState : socket closed")
                return

            try:
                response, ip = self.rcvSock.recvfrom(1024)

            except socket.timeout as ex:
                print("???? ReceiveTelloState : ", ex)      # tello の電源を切っている時、この thread の優先度が高いのか？
                continue

            if b'ok' in response:
                print("**ok")                               # state は、ok を返さない。
                continue

            #print("state = ", response)
            out = response.rstrip(b'\r\n')                  # 最後の改行文字を削除
            out = out.replace( b';', b'\n')
            out = out.replace( b':', b' ' )
            #print(out)
            stateQue.append(out)                            # scratch に送るために queue に積む
            
            sleep(INTERVAL)

    def kill_thread(self):
        self.finishSignal = True

#
# http server for scratch editor
#
class MyHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urlparse(self.path)
        #print(type(parsed_path))

        if parsed_path.path == '/poll':
            #
            # scratch editor が polling した
            #
            if len(stateQue) == 0:
                # print("  empty")
                pass
            else:
                state = stateQue.popleft()
                self.wfile.write( state )                   # state を scrach に
                # print( state )
            return
            
        #print(type(self.path))
        #print(parsed_path.path)
        #print('path = {}'.format(self.path))
        #print('parsed: path = {}, query = {}'.format(parsed_path.path, parse_qs(parsed_path.query)))

        #
        # scratch editor が tello command を送った
        #
        _com = parsed_path.path.replace('/', ' ')
        _com = _com[1:]
        cmdQue.append(_com)



class StartHttpServer(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.start()
        #self.finishSignal = False

    def run(self):
        scratchSvr = HTTPServer(scratchAdrs, MyHTTPRequestHandler)
        try:
            scratchSvr.serve_forever()
        except KeyboardInterrupt:
            print(".... startHttpServer : Exit startHttpServer by KeyboardInterrupt")

    def kill_thread(self):
        print(".... StartHttpServer : shutdown")
        self.scratchSvr.shutdown()
    
#
# 外部からこの thread を停止する方法は分からない
# ctl-c でぬけるはずだが。
#            
class MyInput(threading.Thread):
    def __init__(self):
        self.finishSignal = False   # self.start() の後に書くと run() の self.finish が先に参照されて、no attribute の error が出るようだ
        self.queue = deque()

        super().__init__(daemon=True)
        self.start()

    def run(self):
        while True:
            if self.finishSignal == True:
                #
                # ここが実行されたことはなかった。
                #
                print("....MyInput : close")
                return

            try:
                t = input()

            except Exception as ex:                   # ctl-c でこの例外が発生して、プログラムが終了されるようだ
                print(".... MyInput : ctl-c")         # この行は表示される
                self.queue.append("!!!! ctl-c")       # プログラムが止まるまで時間がかかる
                break
                
            if t != None:
                self.queue.append(t)
            

    def input(self, block=True, timeout=None):
        if len(self.queue) == 0:
            return None
        else:
            return self.queue.popleft()

    #
    # この関数は使わないが、後の参考のために残しておく
    #
    def kill_thread(self):
        self.finishSignal = True


def kill_all_thread():
    sendCmd.kill_thread()
    receiveTelloState.kill_thread()
    #cin.kill_thread()
    #startHttpServer.kill_thread()

if __name__ == "__main__":
    #
    # ここに書いても、上記 class の中で参照できる
    #
    stateQue = deque()         # tello state -> このプログラム -> scratch
    cmdQue   = deque()         # tell cmd    <- このプログラム <- scratch
 
    #
    # all thread start
    #
    sendCmd = SendCmd()        # class なので () は必要。 インスタンスなら() は不要と思われる。
    if sendCmd.result == False:
        print( "\n???? error : can't connect to the tello")
        sys.exit()
        
    receiveTelloState = ReceiveTelloState()
    startHttpServer = StartHttpServer()
    cin = MyInput()

    cmdQue.append('command')
    cmdQue.append('streamon')

    VS_UDP_IP = '0.0.0.0'
    VS_UDP_PORT = 11111
    #udp_video_address = 'udp://@' + VS_UDP_IP + ':' + str(VS_UDP_PORT)
    #cap = cv2.VideoCapture(udp_video_address)
    cap = cv2.VideoCapture(rcvTelloVideoURL)
    #cap.open(udp_video_address)
    cap.open(rcvTelloVideoURL)

    #objects = [ sendCmd, receiveTelloState, startHttpServer, cin ]

    while True:
        try:
            msg = cin.input()

        except KeyboardInterrupt:
            print("....main : Exit KeyboardInterrupt")
            kill_all_thread()                               # どちらか
            # map( lambda func:func.kill_thread(), objects )
            break        

        if msg == None:
            pass
            
        elif '!!!!' in msg:
            break
   
        else:
            if 'quit' in msg:
                print ('.... main : Exit by <quit>')
                kill_all_thread()
                break        

            else:
                cmdQue.append(msg)

        ret, frame = cap.read()
        cv2.imshow('frame', frame)
            
        if cv2.waitKey(1) & 0xFF == ord('q'):
            print ('.... main : Exit by <q> on the video window')
            kill_all_thread()
            break        

    cap.release()
    cv2.destroyAllWindows()

    sendCmd.join()
    print("<<<< join sendCmd")

    receiveTelloState.join()
    print("<<<< join receiveTelloState")

    startHttpServer.kill_thread()
    startHttpServer.join()                 # ここが実行されるとプログラムが終了する
    print("<<<< join startHttpServer")     # ここから下が実行されることはなかった

    cin.join()
    print("<<<< join cin")
