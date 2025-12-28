""" Rnn Parser """
from .MacdParser import *

ModelName = ['CycleLength4', 'CycleChange4', 'BarChange4', 'BarVolume4']


def XColumn():

    cycle_length_x = [Signal, CycleAmplitudeMax, CycleAmplitudePerBar, CycleLengthMax,
                      CycleLengthPerBar, Cycle1mVolMax1, Cycle1mVolMax5, 'volume', Bar1mVolMax1, Bar1mVolMax5,
                      Daily1mVolMax1, Daily1mVolMax5, Daily1mVolMax15]

    cycle_change_x = [Signal, CycleAmplitudeMax, CycleAmplitudePerBar, CycleLengthMax,
                      CycleLengthPerBar, Cycle1mVolMax1, Cycle1mVolMax5, 'volume', Bar1mVolMax1, Bar1mVolMax5,
                      Daily1mVolMax1, Daily1mVolMax5, Daily1mVolMax15]

    bar_change_x = [Signal, preCycleAmplitudeMax, preCycleLengthMax, CycleLengthPerBar,
                    preCycle1mVolMax1, preCycle1mVolMax5, 'volume', Bar1mVolMax1, Bar1mVolMax5, Daily1mVolMax1,
                    Daily1mVolMax5, Daily1mVolMax15]

    bar_volume_x = [Signal, CycleAmplitudeMax, CycleAmplitudePerBar, CycleLengthMax,
                    CycleLengthPerBar, preCycle1mVolMax1, preCycle1mVolMax5, 'volume', Bar1mVolMax1, Bar1mVolMax5]

    columns = (cycle_length_x, cycle_change_x, bar_change_x, bar_volume_x)

    return columns


def YColumn():
    cycle_length_y = [nextCycleLengthMax]
    cycle_change_y = [nextCycleAmplitudeMax]
    bar_change_y = [CycleAmplitudeMax]
    bar_volume_y = ['EndDaily1mVolMax5']
    columns = (cycle_length_y, cycle_change_y, bar_change_y, bar_volume_y)
    return columns


if __name__ == '__main__':
    print(DailyVolEma)
