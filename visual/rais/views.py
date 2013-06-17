from sqlalchemy import func
from flask import Blueprint, request, render_template, flash, g, session, redirect, url_for, jsonify, make_response

from visual import db
from visual.utils import exist_or_404, gzip_data, cached_query, parse_years, Pagination
from visual.rais.models import Yb_rais, Yi, Yo, Ybi, Ybo, Yio, Ybio
from visual.attrs.models import Bra, Isic, Cbo

mod = Blueprint('rais', __name__, url_prefix='/rais')

RESULTS_PER_PAGE = 40

@mod.errorhandler(404)
def page_not_found(error):
    return error, 404

@mod.after_request
def per_request_callbacks(response):
    if response.status_code != 302:
        response.headers['Content-Encoding'] = 'gzip'
        response.headers['Content-Length'] = str(len(response.data))
    return response

def parse_bras(bra_str):
    if "." in bra_str:
        # the '.' indicates we are looking for bras within a given distance
        bra_id, distance = bra_str.split(".")
        bras = exist_or_404(Bra, bra_id)
        neighbors = bras.get_neighbors(distance)
        bras = [g.bra.serialize() for g in neighbors]
    else:
        # we allow the user to specify bras separated by '+'
        bras = bra_str.split("+")
        # Make sure the bra_id requested actually exists in the DB
        bras = [exist_or_404(Bra, bra_id).serialize() for bra_id in bras]
    return bras

def get_query(data_table, url_args, **kwargs):
    query = data_table.query
    order = url_args.get("order", None)
    results_per_page = int(url_args.get("per_page", RESULTS_PER_PAGE))
    if order:
        order = url_args.get("order").split(" ")
    page = url_args.get("page", None)
    join = kwargs["join"] if "join" in kwargs else False
    cache_id = request.path
    ret = {}
    
    # first lets test if this query is cached
    if page is None and order is None:
        cached_q = cached_query(cache_id)
        if cached_q:
            return cached_q
    
    if join:
        join_table = join["table"]
        # check if given a whole table of just a table's column
        if hasattr(join_table, 'parent'):
            join_table = join_table.parent.class_
        query = db.session.query(data_table, join["table"])
        for col in join["on"]:
            query = query.filter(getattr(data_table, col) == getattr(join_table, col))
    
    # handle year (if specified)
    if "year" in kwargs:
        ret["year"] = parse_years(kwargs["year"])
        # filter query
        query = query.filter(data_table.year.in_(ret["year"]))
    
    # handle location (if specified)
    if "bra_id" in kwargs:
        if "show." in kwargs["bra_id"]:
            # the '.' indicates that we are looking for a specific bra nesting
            ret["bra_level"] = kwargs["bra_id"].split(".")[1]
            # filter table by requested nesting level
            query = query.filter(func.char_length(data_table.bra_id) == ret["bra_level"])
        elif "show" not in kwargs["bra_id"]:
            # otherwise we have been given specific bra(s)
            ret["location"] = parse_bras(kwargs["bra_id"])
            # filter query
            if len(ret["location"]) > 1:
                query = query.filter(data_table.bra_id.in_([g["id"] for g in ret["location"]]))
            else:
                query = query.filter(data_table.bra_id == ret["location"][0]["id"])
    
    # handle industry (if specified)
    if "isic_id" in kwargs:
        if "show." in kwargs["isic_id"]:
            # the '.' indicates that we are looking for a specific bra nesting
            ret["isic_level"] = kwargs["isic_id"].split(".")[1]
            # filter table by requested nesting level
            query = query.filter(func.char_length(data_table.isic_id) == ret["isic_level"])
        elif "show" not in kwargs["isic_id"]:
            # we allow the user to specify industries separated by '+'
            ret["industry"] = kwargs["isic_id"].split("+")
            # Make sure the isic_id requested actually exists in the DB
            ret["industry"] = [exist_or_404(Isic, isic_id).serialize() for isic_id in ret["industry"]]
            # filter query
            if len(ret["industry"]) > 1:
                query = query.filter(data_table.isic_id.in_([i["id"] for i in ret["industry"]]))
            else:
                query = query.filter(data_table.isic_id == ret["industry"][0]["id"])

    # handle industry (if specified)
    if "cbo_id" in kwargs:
        if "show." in kwargs["cbo_id"]:
            # the '.' indicates that we are looking for a specific bra nesting
            ret["cbo_level"] = kwargs["cbo_id"].split(".")[1]
            # filter table by requested nesting level
            query = query.filter(func.char_length(data_table.cbo_id) == ret["cbo_level"])
        # make sure the user does not want to show all occupations
        if "show" not in kwargs["cbo_id"]:
            # we allow the user to specify occupations separated by '+'
            ret["occupation"] = kwargs["cbo_id"].split("+")
            # Make sure the cbo_id requested actually exists in the DB
            ret["occupation"] = [exist_or_404(Cbo, cbo_id).serialize() for cbo_id in ret["occupation"]]
            # filter query
            if len(ret["occupation"]) > 1:
                query = query.filter(data_table.cbo_id.in_([o["id"] for o in ret["occupation"]]))
            else:
                query = query.filter(data_table.cbo_id == ret["occupation"][0]["id"])
    
    # handle ordering
    if order:
        for o in order:
            direction = "desc"
            if "." in o:
                o, direction = o.split(".")
            if o == "bra":
                # order by bra
                query = query.join(Bra).order_by(Bra.name_en)
            elif o == "isic":
                # order by isic
                query = query.join(Isic).order_by(Isic.name_en)
            elif o == "cbo":
                # order by cbo
                query = query.join(Cbo).order_by(Cbo.name_en)
            else:
                query = query.order_by(getattr(data_table, o) + " " + direction)
    
    # lastly we want to get the actual data held in the table requested
    if join:
        # items = query.paginate(int(kwargs["page"]), RESULTS_PER_PAGE, False).items
        ret["data"] = []
        if page:
            count = query.count()
            pagination = Pagination(int(page), results_per_page, count)
            items = query.limit(results_per_page).offset(results_per_page * (pagination.page - 1)).all()
            ret["pagination"] = pagination.serialize()
        else:
            items = query.all()
        for row in items:
            datum = row[0].serialize()
            for value, col_name in zip(row[1:], join["columns"].keys()):
                extra = {}
                extra[col_name] = value
                datum = dict(datum.items() + extra.items())
            ret["data"].append(datum)
    elif page:
        count = query.count()
        ret["pagination"] = Pagination(int(page), results_per_page, count).serialize()
        ret["data"] = [d.serialize() for d in query.paginate(int(page), results_per_page, False).items]
    else:
        ret["data"] = [d.serialize() for d in query.all()]
    
    # gzip and jsonify result
    ret = gzip_data(jsonify(ret).data)
    
    if page is None and order is None:
        cached_query(cache_id, ret)
    
    # raise Exception(page)
    return ret

############################################################
# ----------------------------------------------------------
# 2 variable views
# 
############################################################

@mod.route('/all/<bra_id>/all/all/')
@mod.route('/<year>/<bra_id>/all/all/')
def rais_yb(**kwargs):
    return make_response(get_query(Yb_rais, request.args, **kwargs))

@mod.route('/all/all/<isic_id>/all/')
@mod.route('/<year>/all/<isic_id>/all/')
def rais_yi(**kwargs):
    return make_response(get_query(Yi, request.args, **kwargs))

@mod.route('/all/all/all/<cbo_id>/')
@mod.route('/<year>/all/all/<cbo_id>/')
def rais_yo(**kwargs):
    return make_response(get_query(Yo, request.args, **kwargs))

############################################################
# ----------------------------------------------------------
# 3 variable views
# 
############################################################

@mod.route('/all/<bra_id>/<isic_id>/all/')
@mod.route('/<year>/<bra_id>/<isic_id>/all/')
def rais_ybi(**kwargs):
    # if "complexity" in kwargs:
    kwargs["join"] = {
                        "table": Yi.complexity,
                        "columns": {"complexity": Yi.complexity},
                        "on": ('year', 'isic_id')
                    }
    return make_response(get_query(Ybi, request.args, **kwargs))

@mod.route('/all/<bra_id>/all/<cbo_id>/')
@mod.route('/<year>/<bra_id>/all/<cbo_id>/')
def rais_ybo(**kwargs):
    return make_response(get_query(Ybo, request.args, **kwargs))

@mod.route('/all/all/<isic_id>/<cbo_id>/')
@mod.route('/<year>/all/<isic_id>/<cbo_id>/')
def rais_yio(**kwargs):
    return make_response(get_query(Yio, request.args, **kwargs))

############################################################
# ----------------------------------------------------------
# 4 variable views
# 
############################################################

@mod.route('/all/<bra_id>/<isic_id>/<cbo_id>/')
@mod.route('/<year>/<bra_id>/<isic_id>/<cbo_id>/')
def rais_ybio(**kwargs):
    return make_response(get_query(Ybio, request.args, **kwargs))