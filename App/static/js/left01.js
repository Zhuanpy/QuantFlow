var chart_left01 = echarts.init(document.getElementById("left1"), "dark");

var option_left01 = {
	xAxis: {type: 'category',data: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
	},
	yAxis: {type: 'value',
	},
	series: [
		{
			data: [150, 230, 224, 218, 135, 147, 260],
			type: 'line',
		},
	]
};


chart_left01.setOption(option_left01);

