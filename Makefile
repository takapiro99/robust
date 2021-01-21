recv:
	python3 main.py receiver

send:
	python3 main.py sender

send5:
	timeout 5 python3 main.py sender

cmp:
	python3 cmp.py

