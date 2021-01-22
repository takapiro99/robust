from queue import Queue
import socket
import threading
from enum import Enum
import random

from packet import SCUPacketType, SCUHeader, SCUPacket
import utils


class SCUMode(Enum):
    SendMode = 0
    RecvMode = 1


class RecvMode(Enum):
    WaitNewFile = 0
    SendMissingSeqsUntilAnyResponseComes = 1
    RecvUntilEndComes = 2
    RecvUntilFileCompletes = 3  # missing が n 個以下になったら発動することにする
    SendFinUntilNextFileComes = 4


class SendMode(Enum):
    SendNewFile = 0
    KeepSendingDataEndUntilResendReqComes = 1
    SendMissingSeqs = 2
    KeepSendingEndUntilResendReqComes = 3
    SendingMissingSeqLoop = 4  # missing が n 個以下になったら発動。fin来たら次へ。


class NewSCU:
    def __init__(self, mtu=1500):
        self.mtu = mtu
        self.current_fileno = 0

    def bind_as_sender(self, receiver_address):
        self.mode = SCUMode.SendMode
        self.send_mode = SendMode.SendNewFile
        self.connection_manager = {}
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.receiver_address = receiver_address
        self.lock = threading.Lock()

        sender_packet_loop_thread = threading.Thread(
            target=self._sender_packet_loop)
        sender_packet_loop_thread.setDaemon(True)
        sender_packet_loop_thread.start()

    def bind_as_receiver(self, receiver_address):
        self.mode = SCUMode.RecvMode
        self.received_files_data = {}
        self.receive_mode = RecvMode.WaitNewFile
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(receiver_address)

        self.file_received = Queue()
        self.task_manager = Queue()

        receiver_packet_loop_thread = threading.Thread(
            target=self._receiver_packet_loop)
        receiver_packet_loop_thread.setDaemon(True)
        receiver_packet_loop_thread.start()
        recv_controller = threading.Thread(target=self._receiver_controller)
        recv_controller.setDaemon(True)
        recv_controller.start()
        # recv_controller と receiver_packet_loop の二つを回す

    def drop(self):
        if self.mode == SCUMode.SendMode:
            self.connection_manager.clear()
            self.socket.close()

    def _sender_packet_loop(self):
        if self.mode == SCUMode.RecvMode:
            raise Exception
        while True:
            try:
                packet = SCUPacket()
                packet.from_raw(self.socket.recv(2048))
                # psuedo packet loss
                if random.random() >= 0.5:
                    continue
                if packet.header.id not in self.connection_manager:
                    continue  # そのfileじゃないとき
                self.connection_manager[packet.header.id].put(packet)
            except Exception as e:  # recvが失敗した時とputが失敗した時は(適当)
                if e == KeyboardInterrupt:
                    raise KeyboardInterrupt
                else:
                    import traceback
                    traceback.print_exc()

    def send(self, filepath, id):  # will lock the thread
        if self.mode == SCUMode.RecvMode:
            raise Exception
        queue = Queue()
        self.connection_manager[id] = queue  # register queue
        data_fragments = utils.split_file_into_mtu(filepath, self.mtu)
        all_packets = []
        count = 0
        for (seq, df) in enumerate(data_fragments):
            header = SCUHeader()
            if seq == len(data_fragments) - 1:
                header.from_dict(
                    {"typ": SCUPacketType.DataEnd.value, "id": id, "seq": seq, "resendID": 0})
            else:
                header.from_dict(
                    {"typ": SCUPacketType.Data.value, "id": id, "seq": seq, "resendID": 0})
            packet = SCUPacket()
            packet.from_dict({"header": header, "payload": df})
            all_packets.append(packet)
        # created chunked data to send
        # send all data "once" (first of all)
        for seq in range(len(all_packets)):
            with self.lock:  # lock
                self.socket.sendto(
                    all_packets[seq].raw(), self.receiver_address)
        self.send_mode = SendMode.KeepSendingDataEndUntilResendReqComes

        while True:  # main loop
            if self.send_mode == SendMode.SendNewFile:
                pass
            elif self.send_mode == SendMode.KeepSendingDataEndUntilResendReqComes:
                dataEnd = all_packets[-1]
                count = 0
                while True:
                    if count>10: # WIP
                        self.send_mode = SendMode.SendMissingSeqs
                        continue
                    with self.lock:  # lock
                        self.socket.sendto(
                            dataEnd.raw(), self.receiver_address)
                    count += 1
                    try:
                        # TODO: get a packet from queue
                        pass
                    except Exception as e:
                        pass  # no packets in queue
                    else:
                        pass
                        # queueを見て何かしらの再送要求が来てたら
                        # modeをSendMissingSeqsにする
            elif self.send_mode == SendMode.SendMissingSeqs:
                # 言われてた欠損ファイルを一回送る、それか二周送る
                # 送り終わったらmodeをKeepSendingEndUntilResendReqComesにする
                pass
            elif self.send_mode == SendMode.KeepSendingEndUntilResendReqComes:
                # endを送る
                # queueを見て、次の再送要求が来たら
                # もし長さがn以下だったらSendingMissingSeqLoop,
                # そうでなければmodeをSendMissingSeqsにする。
                pass
            elif self.send_mode == SendMode.SendingMissingSeqLoop:
                # TODO: キューからパケットを取り出し、finが来るのを待つ。
                # finが来たら次のファイルに行く。というよりも return する。
                pass
            else:
                raise Exception

    def _receiver_controller(self):
        initial_resendID = 0
        received_files_flag = {}
        file_lengths = {}
        resend_id_count = {}
        while True:  # main loop
            # DataEndが来たらmodeをmissing送るくんに変える。dataendのパケットを破棄し続けつつmissingのパケットを送り続ける。
            # resendIDが一致したものが来たら、modeをrecvUntilEndComesに切り替える
            if self.receive_mode == RecvMode.WaitNewFile:
                try:
                    while True:
                        try:
                            packet, from_addr = self.task_manager.get(
                                block=False)
                            # もし古い情報が来たら、(最新のファイルじゃなかったら)パケットを破棄。continue
                            if self.current_fileno != packet.header.id:
                                continue
                            key = packet.header.id
                        except Exception as e:  # when queue is empty
                            if e == KeyboardInterrupt:
                                raise KeyboardInterrupt
                            else:
                                # print('waiting RecvMode.WaitNewFile but queue was empty')
                                break
                        else:  # when try succeeds. if there are incoming packets
                            # print(packet, from_addr)
                            if key not in self.received_files_data:
                                # 新規登録。記念すべき1seq目
                                self.received_files_data[key] = [b""]*100
                                received_files_flag[key] = False
                            if packet.header.typ == SCUPacketType.DataEnd.value or packet.header.typ == SCUPacketType.Data.value:
                                # just save
                                self.received_files_data[key][packet.header.seq] = packet.payload
                                # dataEndだったら保存し、次のモードへ。 break.
                                if packet.header.typ == SCUPacketType.DataEnd.value:
                                    if key not in file_lengths:
                                        print('received dataEnd of file:', key)
                                        file_lengths[key] = packet.header.seq + 1
                                        resend_id_count[key] = initial_resendID
                                        # missing seqs
                                        unreceived_seqs_str = self.calculate_rtr(
                                            key, packet.header.seq)
                                        print(unreceived_seqs_str)
                                        self.receive_mode = RecvMode.SendMissingSeqsUntilAnyResponseComes
                            else:
                                pass  # ありえんかもー
                except Exception as e:  # sendtoが失敗した時は(適当)
                    if e == KeyboardInterrupt:
                        raise KeyboardInterrupt
                    else:
                        import traceback
                        traceback.print_exc()
            elif self.receive_mode == RecvMode.SendMissingSeqsUntilAnyResponseComes:
                pass
                # 再送要求を送る
                # queueを見てresendIDを含むものが来てたら送るのをやめる。
                # そして RecvUntilEndComes にモードを変える。
            elif self.receive_mode == RecvMode.RecvUntilEndComes:
                # print("recvUntilEndComes")
                pass
                # endが来るまでqueueから取り出し、保存する。
                # endを見つけたら、その時点で足りないパケットの数がn個以下だったら
                #    # RecvUntilFileCompletesに、
                # そうでなければ SendMissingSeqsUntilAnyResponseComes に変える。
            elif self.receive_mode == RecvMode.RecvUntilFileCompletes:
                print("aaa")
                # パケットが来るごとに全部出来上がったかを確認し、
                # 完成したら SendFinUntilNextFileComes に変える。
                # そしてこれ
                # self.file_received.put((key, received_files_length[key]))
            else:
                raise Exception

    # receives packet, unpack it and add to Queue (task_manager)
    # adds (packet, from_addr) to queue
    def _receiver_packet_loop(self):
        if self.mode == SCUMode.SendMode:
            raise Exception
        while True:
            try:
                data, from_addr = self.socket.recvfrom(2048)
                # psuedo packet loss
                if random.random() >= 0.5:
                    continue
                packet = SCUPacket()
                packet.from_raw(data)
                # key = utils.endpoint2str(from_addr, packet.header.id)
                # putting it to task_manager Queue() (<Packet>, fileno, from_addr)
                self.task_manager.put(
                    (packet, from_addr))
            except Exception as e:  # when recv fails
                if e == KeyboardInterrupt:
                    raise KeyboardInterrupt
                else:
                    import traceback
                    traceback.print_exc()

    def calculate_rtr(self, key, seq):
        unreceived_seqs = []
        for sq in range(0, seq):
            if not self.received_files_data[key][sq]:
                unreceived_seqs.append(str(sq))
        return ",".join(unreceived_seqs)

    def is_all_received(self, key, length):
        for i in range(0, length):
            if not self.received_files_data[key][i]:
                return False
        return True

    def response(self, typ, addr, id, rtr, resendID, *content):
        if self.mode == SCUMode.SendMode:
            raise Exception
        if typ == SCUPacketType.Rtr.value:
            header = SCUHeader()
            header.from_dict(
                {"typ": typ, "id": id, "seq": rtr, "resendID": resendID})
            packet = SCUPacket()
            payload = content if content else b''
            packet.from_dict({"header": header, "payload": payload, })
            self.socket.sendto(packet.raw(), addr)

        elif typ == SCUPacketType.Fin.value:
            header = SCUHeader()
            header.from_dict(
                {"typ": typ, "id": id, "seq": rtr, "resendID": resendID})
            packet = SCUPacket()
            packet.from_dict({"header": header, "payload": b'', })
            self.socket.sendto(packet.raw(), addr)

    def recv(self):
        if self.mode == SCUMode.SendMode:
            raise Exception
        key, length = self.file_received.get()
        return utils.fold_data(self.received_files_data[key], length)


"""

1. Taro attempts to send file: 123456789
2. Hanako receives broken data: 12**56**9
3. Hanako requests 3478 (looping)
4. Taro receives resend request
5. Taro then sends 3,4,7,8,8,8,8,8,8
6. Hanako receives 3**8
7. Hanako requests 47
8. Taro Sends 4,7,4,7,4,7,4,7,...
9. Hanako Sends <Fin>
10. Taro then goes to next file.

resend req packets: header(typ, fileno)/payload(0,1,2,3,4,5,6,7,8,11,12,13,14,15,16,17,19,20,25,26,43,44,46,47,48,49,50,53,54,56,59,62,64,65,66,67)

"""
