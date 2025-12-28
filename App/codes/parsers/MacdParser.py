from config import Config

col = Config.STOCK_COLUMNS

# signal
Signal = col['Signal']['1']
SignalId = col['Signal']['2']
SignalChoice = col['Signal']['3']
SignalStartIndex = col['Signal']['4']
up = col['Signal']['5']
down = col['Signal']['6']

upInt = col['Signal']['7']
downInt = col['Signal']['8']

# Macd
EmaShort = col['Macd']['1']
EmaMid = col['Macd']['2']
EmaLong = col['Macd']['3']
Dif = col['Macd']['4']
DifSm = col['Macd']['5']
DifMl = col['Macd']['6']
Dea = col['Macd']['7']
macd_ = col['Macd']['8']

# boll

# cycle
EndPrice = col['cycle']['1']
EndPriceIndex = col['cycle']['2']
StartPrice = col['cycle']['3']
StartPriceIndex = col['cycle']['4']

Cycle1mVolMax1 = col['cycle']['5']
Cycle1mVolMax5 = col['cycle']['6']

Bar1mVolMax1 = col['cycle']['7']
Bar1mVolMax5 = col['cycle']['8']
CycleLengthMax = col['cycle']['9']
CycleLengthPerBar = col['cycle']['10']
CycleAmplitudePerBar = col['cycle']['11']
CycleAmplitudeMax = col['cycle']['12']

# Recycle
preCycle1mVolMax1 = col['Recycle']['1']
preCycle1mVolMax5 = col['Recycle']['2']
preCycleAmplitudeMax = col['Recycle']['3']
preCycleLengthMax = col['Recycle']['4']
nextCycleLengthMax = col['Recycle']['5']
nextCycleAmplitudeMax = col['Recycle']['6']

# "Signal30m"
Signal30m = col['Signal30m']['1']
SignalChoice30m = col['Signal30m']['2']
SignalTimes30m = col['Signal30m']['3']

# "Signal120m"
Signal120m = col['Signal120m']['1']
SignalChoice120m = col['Signal120m']['2']
SignalTimes120m = col['Signal120m']['3']

# "SignalDaily":
Daily1mVolMax1 = col['SignalDaily']['1']
Daily1mVolMax5 = col['SignalDaily']['2']
Daily1mVolMax15 = col['SignalDaily']['3']
DailyVolEmaParser = col['SignalDaily']['4']
DailyVolEma = col['SignalDaily']['5']


if __name__ == '__main__':
    print(DailyVolEma)
