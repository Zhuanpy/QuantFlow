function get_c1_data(){
		$.ajax({
			
			url:"/c1",
			
			success:function(data){
				$(".num h1").eq(0).text(data.confirm);
				$(".num h1").eq(1).text(data.suspect);
				$(".num h1").eq(2).text(data.heal);
				$(".num h1").eq(3).text(data.dead);
			},
			
		})
	}
			

function gettime(){
	$.ajax({
		url:"/timing",
		timeout:10000,//超时时间设置为10秒；
		success:function(data){
			$("#tim").html(data)
		},
		error:function(xhr,type,errorThrown){
			
		}
	});
}
			
setInterval(gettime, 1000)
setInterval(get_c1_data, 1000)