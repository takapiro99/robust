arr = [i for i in range(15)]

print(arr)
for sq in arr:
  if sq==6:
    for i in range(sq+1, len(arr)):
      print(i)
# for i in range()