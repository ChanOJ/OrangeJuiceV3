var mX, mY;

function recordmousecoordinates(evnt) {
	mX = evnt.pageX;
	mY = evnt.pageY;
}

function dhtmlpopup()
{
	var args;
	args = dhtmlpopup.arguments;

	if (args[1] == 2)
	{
		if (navigator.appName == 'Netscape')
			document.layers[args[0]].visibility = 'show';
		else if (document.all)
			document.all[args[0]].style.visibility = 'visible';
		else if (document.getElementById)
			document.getElementById(args[0]).style.visibility = 'visible';
	}
	else if (args[1] == 1)
	{
		if (document.layers)
		{ // NS
			document.layers[args[0]].pageX = mX - (document.layers[args[0]].clip.width * args[2]) - args[4];
			document.layers[args[0]].pageY = mY - (document.layers[args[0]].clip.height * args[3]) - args[5];
			document.layers[args[0]].visibility = 'show';
		}
		else if (document.all)
		{ // IE
			document.all[args[0]].style.posLeft = window.event.x + document.body.scrollLeft - (document.all[args[0]].clientWidth * args[2]) - args[4];
			document.all[args[0]].style.posTop = window.event.y + document.body.scrollTop - (document.all[args[0]].clientHeight * args[3]) - args[5];
			document.all[args[0]].style.visibility = 'visible';
		}
		else if (document.getElementById)
		{ // MZ6
			//document.getElementById(args[0]).style.posLeft = window.event.x + document.body.scrollLeft - (document.getElementById(args[0]).clientWidth * args[2]) - args[4];
			//document.getElementById(args[0]).style.posTop = window.event.y + document.body.scrollTop - (document.getElementById(args[0]).clientHeight * args[3]) - args[5];
			//document.getElementById(args[0]).style.visibility = 'visible';
		}
	}
	else
	{
		if (document.layers)
			document.layers[args[0]].visibility = 'hide';
		else if (document.all)
			document.all[args[0]].style.visibility = 'hidden';
		else if (document.getElementById)
			document.getElementById(args[0]).style.visibility = 'hidden';
	}
}

function loginpopup()
{
	loginwin = window.open('/_a/loginlist.htm','loginwin','toolbar=0,location=0,directories=0,status=1,menubar=0,scrollbars=1,resizable=1,width=450,height=400,left=' + (window.screen.width-450)/2 + ',top=' + (window.screen.height-400)/2);
	loginwin.focus();
}

function grouppopup(f)
{
	loginwin = window.open('/groups/a/groupslist.htm?field='+f,'loginwin','toolbar=0,location=0,directories=0,status=1,menubar=0,scrollbars=1,resizable=1,width=450,height=400,left=' + (window.screen.width-450)/2 + ',top=' + (window.screen.height-400)/2);
	loginwin.focus();
}

function newmsgpopup(id) {
	msgpop = window.open('/message_write.htm?idlogin='+id,'msgpopup','toolbar=0,location=0,directories=0,status=0,menubar=0,scrollbars=1,resizable=1,width=500,height=300,left=' + (window.screen.width-500)/2 + ',top=' + (window.screen.height-300)/2);
}

function s(p) { window.status=p; return true; }
function n() { window.status=''; return true; }

if (navigator.appName == 'Netscape')
{
	document.captureEvents(Event.MOUSEMOVE);
	document.onmousemove = recordmousecoordinates;
}
